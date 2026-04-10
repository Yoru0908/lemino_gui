#!/usr/bin/env python3
"""
Lemino 番組自動追跡器

定時チェック → 新集発見 → ダウンロード → Alist アップロード → NapCat QQ 推送

内容構造:
  WIZARD (系列聚合) → SERIES (季度分組, 3ヶ月) → PIT (単集 VOD)
  PIT 同士は previous_content / next_content で連結 (季度跨ぎも無縫)

追跡戦略:
  各番組の最後の既知 VOD CRID を記録。
  定期的に next_content をチェック。
  next_content != None → 新集 → ダウンロード → アップロード → プッシュ

部署:
  Homeserver PM2: pm2 start lemino_watcher.py --name lemino-watcher --interpreter python3
  或 Cron: 0 * * * * cd /path/to/lemino && python3 lemino_watcher.py --once

配置:
  複製 watcher_config.example.json → watcher_config.json
"""
import requests
import json
import time
import sys
import os
import re
import subprocess
import logging
import uuid
import shutil
from pathlib import Path
from urllib.parse import quote
from datetime import datetime

# ============================================================
# 路径 & 日志
# ============================================================

SCRIPT_DIR = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "watcher_config.json"
STATE_PATH = SCRIPT_DIR / "watcher_state.json"
TOKEN_PATH = SCRIPT_DIR / ".token"
LOG_DIR = SCRIPT_DIR / "logs"

LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "watcher.log", encoding="utf-8"),
        logging.StreamHandler(),
    ]
)
log = logging.getLogger("lemino_watcher")

# ============================================================
# Lemino API 常量
# ============================================================

LEMINO_API_BASE = "https://if.lemino.docomo.ne.jp"
HEADERS = {
    "Origin": "https://lemino.docomo.ne.jp",
    "Referer": "https://lemino.docomo.ne.jp/",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
}

# ============================================================
# 配置 & 状態管理
# ============================================================

def load_config():
    if not CONFIG_PATH.exists():
        log.error(f"配置文件不存在: {CONFIG_PATH}")
        log.error("請複製 watcher_config.example.json → watcher_config.json 並填入参数")
        sys.exit(1)
    with open(CONFIG_PATH) as f:
        return json.load(f)


def load_state():
    if STATE_PATH.exists():
        with open(STATE_PATH) as f:
            return json.load(f)
    return {"shows": {}}


def save_state(state):
    with open(STATE_PATH, 'w') as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def load_token():
    """从 .token 文件加载 x-service-token"""
    if not TOKEN_PATH.exists():
        return None
    try:
        data = json.loads(TOKEN_PATH.read_text().strip())
        return data.get("x-service-token", "").strip()
    except Exception:
        # 也许是纯文本格式
        return TOKEN_PATH.read_text().strip()

# ============================================================
# Lemino API 查询
# ============================================================

def query_meta(crid, token):
    """查询单个 CRID 的 meta 信息"""
    headers = {
        **HEADERS,
        "x-service-token": token,
        "x-trace-id": str(uuid.uuid4()),
    }
    r = requests.get(
        f"{LEMINO_API_BASE}/v1/meta/contents?crid={quote(crid)}",
        headers=headers,
        timeout=15,
    )
    if r.status_code != 200:
        log.warning(f"API error {r.status_code} for {crid}")
        return None
    d = r.json()
    if d.get("result") != "0" or not d.get("meta_list"):
        return None
    return d["meta_list"][0]


def check_new_episode(show_name, last_crid, token):
    """
    检查是否有新集。
    查询 last_crid 的 next_content，如果不为 None 则有新集。
    返回新集信息 dict 或 None。
    """
    meta = query_meta(last_crid, token)
    if not meta:
        log.warning(f"{show_name}: 无法查询 {last_crid} (token 可能过期)")
        return None

    next_crid = meta.get("next_content")
    if not next_crid:
        return None

    # 获取新集详细信息
    time.sleep(0.5)
    new_meta = query_meta(next_crid, token)
    if not new_meta:
        log.warning(f"{show_name}: next_content {next_crid} 查询失败")
        return None

    cid_objs = new_meta.get("cid_obj") or []
    cid = cid_objs[0].get("cid") if cid_objs else None

    # 获取文件大小 (quality 5 = 最高画质)
    size_mb = 0
    for c in cid_objs:
        for ds in c.get("download_size", []):
            if ds.get("quality") == 5:
                size_mb = ds.get("bytes", 0) / 1024 / 1024

    title = new_meta.get("title", "")
    series_parent = new_meta.get("series_parent", [])

    return {
        "crid": next_crid,
        "cid": cid,
        "title": title,
        "size_mb": size_mb,
        "series_parent": series_parent,
        "duration_sec": new_meta.get("duration_sec", 0),
    }

# ============================================================
# Alist 上传
# ============================================================

class AlistClient:
    def __init__(self, config):
        self.base_url = config.get("alist_url", "").rstrip("/")
        self.username = config.get("alist_username", "")
        self.password = config.get("alist_password", "")
        self.public_url = config.get("alist_public_url", self.base_url).rstrip("/")
        self.token = None

    def login(self):
        if not self.base_url:
            return False
        try:
            r = requests.post(f"{self.base_url}/api/auth/login", json={
                "username": self.username, "password": self.password
            }, timeout=10)
            data = r.json()
            if data.get("code") == 200:
                self.token = data["data"]["token"]
                log.info("Alist 登录成功")
                return True
            else:
                log.error(f"Alist 登录失败: {data.get('message')}")
                return False
        except Exception as e:
            log.error(f"Alist 连接失败: {e}")
            return False

    def upload(self, remote_path, local_path):
        """上传本地文件到 Alist"""
        if not self.token:
            return False
        try:
            encoded_path = quote(remote_path, safe="/")
            file_size = Path(local_path).stat().st_size
            with open(local_path, "rb") as f:
                r = requests.put(
                    f"{self.base_url}/api/fs/put",
                    headers={
                        "Authorization": self.token,
                        "File-Path": encoded_path,
                        "Content-Type": "application/octet-stream",
                        "Content-Length": str(file_size),
                    },
                    data=f,
                    timeout=600,  # 10 min for large video
                )
            data = r.json()
            if data.get("code") == 200:
                return True
            else:
                log.warning(f"Alist 上传失败 {remote_path}: {data.get('message')}")
                return False
        except Exception as e:
            log.warning(f"Alist 上传异常 {remote_path}: {e}")
            return False

    def upload_bytes(self, remote_path, data_bytes):
        """上传 bytes 数据到 Alist (用于 metadata.json 等小文件)"""
        if not self.token:
            return False
        try:
            encoded_path = quote(remote_path, safe="/")
            r = requests.put(
                f"{self.base_url}/api/fs/put",
                headers={
                    "Authorization": self.token,
                    "File-Path": encoded_path,
                    "Content-Type": "application/octet-stream",
                    "Content-Length": str(len(data_bytes)),
                },
                data=data_bytes,
                timeout=30,
            )
            data = r.json()
            return data.get("code") == 200
        except Exception as e:
            log.warning(f"Alist upload_bytes 异常 {remote_path}: {e}")
            return False

    def download_json(self, remote_path):
        """从 Alist 下载 JSON 文件"""
        try:
            encoded_path = quote(remote_path, safe="/")
            r = requests.post(
                f"{self.base_url}/api/fs/get",
                json={"path": remote_path},
                headers={"Authorization": self.token} if self.token else {},
                timeout=10,
            )
            data = r.json()
            if data.get("code") != 200:
                return None
            raw_url = data["data"].get("raw_url", "")
            if raw_url:
                r2 = requests.get(raw_url, timeout=10)
                if r2.status_code == 200:
                    return r2.json()
        except Exception:
            pass
        return None

    def get_public_url(self, remote_path):
        encoded_path = quote(remote_path, safe="/")
        return f"{self.public_url}/d{encoded_path}"

# ============================================================
# NapCat QQ 推送
# ============================================================

def push_napcat(config, show_name, ep_info, alist_url=None):
    """推送新集通知到 QQ 群"""
    napcat_url = config.get("napcat_http_url", "")
    napcat_token = config.get("napcat_http_token", "")
    group_ids = config.get("push_group_ids", [])

    if not napcat_url or not group_ids:
        return

    headers = {}
    if napcat_token:
        headers["Authorization"] = f"Bearer {napcat_token}"

    duration = ep_info.get("duration_sec", 0)
    duration_str = f"{duration // 60}分{duration % 60}秒" if duration else ""

    text = f"📺 Lemino 新集\n{show_name}\n{ep_info['title']}"
    if duration_str:
        text += f"\n時間: {duration_str}"
    if ep_info.get("size_mb"):
        text += f"\nサイズ: {ep_info['size_mb']:.0f}MB"
    if alist_url:
        text += f"\n🔗 {alist_url}"

    message = [{"type": "text", "data": {"text": text}}]

    for gid in group_ids:
        for attempt in range(2):
            try:
                r = requests.post(
                    f"{napcat_url}/send_group_msg",
                    json={"group_id": int(gid), "message": message},
                    headers=headers,
                    timeout=30,
                )
                resp = r.json() if r.status_code == 200 else {}
                if resp.get("retcode") == 0:
                    log.info(f"  推送群 {gid}: {ep_info['title']}")
                    break
                else:
                    log.warning(f"  推送失败 群{gid} (尝试{attempt+1}): {resp.get('message', '')[:80]}")
            except Exception as e:
                log.warning(f"  推送异常 群{gid} (尝试{attempt+1}): {e}")
            time.sleep(3)
        time.sleep(2)

# ============================================================
# 文件名清理
# ============================================================

def sanitize_filename(title):
    """清理标题中的特殊字符，用于文件名"""
    # 替换全角符号
    title = title.replace("？", "?").replace("【", "[").replace("】", "]")
    title = title.replace("／", "/").replace("　", " ")
    # 移除文件系统不安全字符
    title = re.sub(r'[<>:"/\\|?*]', '_', title)
    # 压缩连续空格和下划线
    title = re.sub(r'[_ ]+', '_', title).strip("_ ")
    return title

# ============================================================
# 下载
# ============================================================

def download_episode(cid, output_path, quality=1080, token=None, crid=None, group_crid=None):
    """
    调用 lemino_dl.py 下载视频。
    返回 (success, stdout, stderr)
    """
    cmd = [
        sys.executable,
        str(SCRIPT_DIR / "lemino_dl.py"),
        cid,
        "--quality", str(quality),
        "-o", str(output_path),
    ]
    if token:
        cmd.extend(["--token", token])
    if crid:
        cmd.extend(["--crid", crid])
    if group_crid:
        cmd.extend(["--group-crid", group_crid])
    log.info(f"  下载命令: {' '.join(cmd)}")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=3600,  # 1 hour timeout
            cwd=str(SCRIPT_DIR),
        )
        if result.returncode == 0:
            return True, result.stdout, result.stderr
        else:
            return False, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        log.error("  下载超时 (1 hour)")
        return False, "", "timeout"
    except Exception as e:
        log.error(f"  下载异常: {e}")
        return False, "", str(e)

# ============================================================
# 主逻辑
# ============================================================

def process_show(show_config, state, global_config, token):
    """
    处理单个节目: 检查新集 → 下载 → 上传 → 推送。
    支持链式追踪（一次检查中发现多集新集）。
    """
    show_name = show_config["name"]
    quality = show_config.get("quality", global_config.get("quality", 1080))

    show_state = state.setdefault("shows", {}).setdefault(show_name, {})
    last_crid = show_state.get("last_crid")

    if not last_crid:
        log.warning(f"{show_name}: state 中没有 last_crid，跳过 (请先运行 --init)")
        return 0

    processed = 0
    max_chain = 5  # 一次最多追 5 集 (防止无限循环)

    for chain_step in range(max_chain):
        ep = check_new_episode(show_name, last_crid, token)
        if not ep:
            if chain_step == 0:
                log.info(f"{show_name}: 暂无新集")
            break

        log.info(f"{'='*50}")
        log.info(f"🆕 {show_name}: {ep['title']}")
        log.info(f"   CID: {ep['cid']}  大小: {ep['size_mb']:.0f}MB")

        if not ep["cid"]:
            log.error(f"  新集没有 CID，可能尚未上线")
            break

        # 构建输出路径
        download_dir = Path(global_config.get("download_dir", str(SCRIPT_DIR / "downloads")))
        download_dir.mkdir(parents=True, exist_ok=True)
        safe_title = sanitize_filename(ep["title"])
        output_file = download_dir / f"{safe_title}.mp4"

        # 下载
        log.info(f"  下载中: {output_file.name}")
        ok, stdout, stderr = download_episode(ep["cid"], output_file, quality, token=token, crid=ep.get("crid"))

        if not ok:
            log.error(f"  下载失败!")
            if stderr:
                log.error(f"  stderr (last 500): {stderr[-500:]}")
            break

        file_size_mb = output_file.stat().st_size / 1024 / 1024
        log.info(f"  下载完成: {file_size_mb:.1f}MB")

        # 验证文件大小 (至少 10MB)
        if file_size_mb < 10:
            log.warning(f"  文件过小 ({file_size_mb:.1f}MB)，可能下载不完整")
            output_file.unlink(missing_ok=True)
            break

        # 上传 Alist
        alist_url_str = None
        alist_upload_base = global_config.get("alist_upload_base", "/lemino")
        alist_remote = f"{alist_upload_base}/{show_name}/{safe_title}.mp4"

        if global_config.get("alist_url"):
            alist = AlistClient(global_config)
            if alist.login():
                log.info(f"  上传 Alist: {alist_remote}")
                if alist.upload(alist_remote, output_file):
                    alist_url_str = alist.get_public_url(alist_remote)
                    log.info(f"  上传成功: {alist_url_str}")

                    # 更新 metadata.json (集数标题等元数据)
                    meta_remote = f"{alist_upload_base}/{show_name}/metadata.json"
                    existing_meta = alist.download_json(meta_remote) or {"episodes": {}}
                    existing_meta["episodes"][f"{safe_title}.mp4"] = {
                        "title": ep["title"],
                        "epNum": ep.get("crid", "").split("_")[-1] if "_" in ep.get("crid", "") else "",
                        "duration_sec": ep.get("duration_sec", 0),
                        "size_mb": ep.get("size_mb", 0),
                        "crid": ep.get("crid", ""),
                        "date": datetime.now().strftime("%Y-%m-%d"),
                    }
                    meta_bytes = json.dumps(existing_meta, ensure_ascii=False, indent=2).encode("utf-8")
                    if alist.upload_bytes(meta_remote, meta_bytes):
                        log.info(f"  metadata.json 已更新")
                    else:
                        log.warning(f"  metadata.json 更新失败")

                    # 上传成功后删除本地文件
                    if not global_config.get("keep_downloads", False):
                        output_file.unlink(missing_ok=True)
                        log.info(f"  已删除本地文件")
                else:
                    log.warning(f"  Alist 上传失败，保留本地文件")

        # 推送 NapCat
        push_napcat(global_config, show_name, ep, alist_url=alist_url_str)

        # 更新 state
        last_crid = ep["crid"]
        show_state["last_crid"] = ep["crid"]
        show_state["last_title"] = ep["title"]
        show_state["last_cid"] = ep["cid"]
        show_state["last_download"] = datetime.now().isoformat()
        save_state(state)
        processed += 1

        log.info(f"  ✅ {ep['title']} 处理完成")
        time.sleep(2)

    return processed


def run_once(config):
    """单次检查所有节目"""
    token = load_token()
    if not token:
        log.error("无可用 token — 请确认 .token 文件存在")
        log.error("  M1: 运行 token_refresh.sh 或 auto_login.py")
        return False

    log.info(f"Token: {token[:8]}...")
    state = load_state()
    total = 0

    for show in config.get("shows", []):
        try:
            n = process_show(show, state, config, token)
            total += n
        except Exception as e:
            log.error(f"{show['name']}: 异常 {e}", exc_info=True)
        time.sleep(1)

    if total > 0:
        log.info(f"本次共处理 {total} 集新节目")
    return True

# ============================================================
# Main
# ============================================================

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Lemino 番組自動追跡器")
    parser.add_argument("--once", action="store_true", help="単次チェック後終了 (Cron 向け)")
    parser.add_argument("--interval", type=int, default=None, help="チェック間隔 (分), config を上書き")
    parser.add_argument("--init", action="store_true", help="初期化: 現在の最新集を state に記録 (ダウンロードしない)")
    args = parser.parse_args()

    config = load_config()

    if args.init:
        log.info("=== 初始化模式 ===")
        token = load_token()
        if not token:
            log.error("无可用 token")
            sys.exit(1)

        state = load_state()
        for show in config.get("shows", []):
            show_name = show["name"]
            init_crid = show.get("init_crid")
            if not init_crid:
                log.warning(f"{show_name}: 配置中缺少 init_crid，跳过")
                continue

            # 验证 CRID 是否有效
            meta = query_meta(init_crid, token)
            if meta:
                title = meta.get("title", "?")
                state.setdefault("shows", {}).setdefault(show_name, {}).update({
                    "last_crid": init_crid,
                    "last_title": title,
                    "last_check": datetime.now().isoformat(),
                })
                log.info(f"  {show_name}: 初始化为 {title}")
                log.info(f"    CRID: {init_crid}")
            else:
                log.error(f"  {show_name}: CRID {init_crid} 无效")
            time.sleep(0.5)

        save_state(state)
        log.info("初始化完成")
        return

    if args.once:
        log.info("=== 单次检查 ===")
        success = run_once(config)
        if not success:
            sys.exit(1)
        return

    # 守护模式
    interval = args.interval or config.get("check_interval_minutes", 60)
    log.info(f"{'='*50}")
    log.info(f"  Lemino Watcher 守護モード")
    log.info(f"  チェック間隔: {interval} 分")
    log.info(f"  番組数: {len(config.get('shows', []))}")
    log.info(f"{'='*50}")

    while True:
        try:
            run_once(config)
        except Exception as e:
            log.error(f"检查异常: {e}", exc_info=True)

        log.info(f"次回チェック: {interval} 分後")
        time.sleep(interval * 60)


if __name__ == "__main__":
    main()
