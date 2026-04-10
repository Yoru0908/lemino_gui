#!/usr/bin/env python3
"""
Lemino 批量下载 — 从最新集往回追溯所有可用集数，全部下载上传到 Alist。
跳过 Alist 上已存在的文件。

用法:
  python3 batch_download.py --show そこ曲がったら櫻坂
  python3 batch_download.py --show ちょこさく
  python3 batch_download.py --all
"""
import sys, os, json, time, re, logging, argparse
from pathlib import Path
from datetime import datetime

# 复用 watcher 的函数
sys.path.insert(0, str(Path(__file__).parent))
from lemino_watcher import (
    query_meta, download_episode, sanitize_filename,
    AlistClient, load_token, load_config,
    SCRIPT_DIR
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [BATCH] %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger(__name__)


def trace_all_episodes(start_crid, token, direction='prev'):
    """从 start_crid 往前/后追溯所有集，返回 [{crid, title, cid, ...}, ...]"""
    episodes = []
    current = start_crid
    seen = set()

    while current and current not in seen:
        seen.add(current)
        meta = query_meta(current, token)
        if not meta:
            log.warning(f"  无法查询 {current}，停止追溯")
            break

        cid_objs = meta.get("cid_obj") or []
        cid = cid_objs[0].get("cid") if cid_objs else None

        member_of = meta.get("member_of", [])
        group_crid = member_of[0] if member_of else None

        episodes.append({
            "crid": current,
            "cid": cid,
            "title": meta.get("title", ""),
            "duration_sec": meta.get("duration_sec", 0),
            "group_crid": group_crid,
        })

        key = "previous_content" if direction == 'prev' else "next_content"
        current = meta.get(key)
        time.sleep(0.3)

    return episodes


def list_alist_files(alist, path):
    """列出 Alist 目录下的文件名"""
    try:
        import requests
        r = requests.post(
            f"{alist.base_url}/api/fs/list",
            json={"path": path, "page": 1, "per_page": 500},
            headers={"Authorization": alist.token} if alist.token else {},
            timeout=10,
        )
        data = r.json()
        if data.get("code") == 200:
            return {f["name"] for f in (data["data"].get("content") or [])}
    except Exception as e:
        log.warning(f"列出 Alist 文件失败: {e}")
    return set()


def batch_download_show(show_name, start_crid, config, token, dl_token):
    """批量下载一个节目的所有集"""
    log.info(f"{'='*60}")
    log.info(f"  批量下载: {show_name}")
    log.info(f"  起始 CRID: {start_crid}")
    log.info(f"{'='*60}")

    # 1. 追溯所有集 (往前)
    log.info("Step 1: 往前追溯所有集...")
    prev_eps = trace_all_episodes(start_crid, token, direction='prev')
    log.info(f"  往前找到 {len(prev_eps)} 集 (含起始集)")

    # 2. 追溯所有集 (往后，跳过起始集)
    meta = query_meta(start_crid, token)
    next_crid = meta.get("next_content") if meta else None
    next_eps = []
    if next_crid:
        log.info("Step 2: 往后追溯...")
        next_eps = trace_all_episodes(next_crid, token, direction='next')
        log.info(f"  往后找到 {len(next_eps)} 集")

    # 3. 合并，按时间正序
    all_eps = list(reversed(prev_eps)) + next_eps
    log.info(f"总计: {len(all_eps)} 集可用")

    if not all_eps:
        log.warning("无可用集数")
        return

    # 打印集数列表
    for i, ep in enumerate(all_eps):
        log.info(f"  [{i+1}/{len(all_eps)}] {ep['title']}")

    # 4. 准备 Alist
    alist = None
    alist_upload_base = config.get("alist_upload_base", "/lemino")
    alist_dir = f"{alist_upload_base}/{show_name}"
    existing_files = set()

    if config.get("alist_url"):
        alist = AlistClient(config)
        if alist.login():
            existing_files = list_alist_files(alist, alist_dir)
            log.info(f"Alist 已有 {len(existing_files)} 个文件")
        else:
            log.error("Alist 登录失败")
            return

    # 5. 逐集下载
    quality = config.get("quality", 1080)
    download_dir = Path(config.get("download_dir", str(SCRIPT_DIR / "downloads")))
    download_dir.mkdir(parents=True, exist_ok=True)

    downloaded = 0
    skipped = 0
    failed = 0

    for i, ep in enumerate(all_eps):
        safe_title = sanitize_filename(ep["title"])
        filename = f"{safe_title}.mp4"

        # 跳过已存在的
        if filename in existing_files:
            log.info(f"  [{i+1}/{len(all_eps)}] 跳过 (已存在): {ep['title']}")
            skipped += 1
            continue

        if not ep["cid"]:
            log.warning(f"  [{i+1}/{len(all_eps)}] 跳过 (无CID): {ep['title']}")
            skipped += 1
            continue

        log.info(f"  [{i+1}/{len(all_eps)}] 下载: {ep['title']}")

        output_file = download_dir / filename
        ok, stdout, stderr = download_episode(ep["cid"], output_file, quality, token=dl_token, crid=ep.get("crid"), group_crid=ep.get("group_crid"))

        if not ok:
            log.error(f"    下载失败! {stderr[-200:] if stderr else ''}")
            failed += 1
            continue

        file_size_mb = output_file.stat().st_size / 1024 / 1024
        if file_size_mb < 10:
            log.warning(f"    文件过小 ({file_size_mb:.1f}MB)，跳过")
            output_file.unlink(missing_ok=True)
            failed += 1
            continue

        log.info(f"    下载完成: {file_size_mb:.1f}MB")

        # 上传 Alist
        if alist:
            alist_remote = f"{alist_dir}/{filename}"
            log.info(f"    上传 Alist...")
            if alist.upload(alist_remote, output_file):
                log.info(f"    上传成功")
                # 更新 metadata.json
                meta_remote = f"{alist_dir}/metadata.json"
                existing_meta = alist.download_json(meta_remote) or {"episodes": {}}
                existing_meta["episodes"][filename] = {
                    "title": ep["title"],
                    "duration_sec": ep.get("duration_sec", 0),
                    "crid": ep.get("crid", ""),
                    "date": datetime.now().strftime("%Y-%m-%d"),
                }
                meta_bytes = json.dumps(existing_meta, ensure_ascii=False, indent=2).encode("utf-8")
                alist.upload_bytes(meta_remote, meta_bytes)

                # 删除本地文件
                if not config.get("keep_downloads", False):
                    output_file.unlink(missing_ok=True)
            else:
                log.warning(f"    上传失败，保留本地文件")

        downloaded += 1
        time.sleep(2)  # 防止请求过快

    log.info(f"{'='*60}")
    log.info(f"  完成! 下载: {downloaded}, 跳过: {skipped}, 失败: {failed}")
    log.info(f"{'='*60}")


def main():
    parser = argparse.ArgumentParser(description="Lemino 批量下载")
    parser.add_argument("--show", type=str, help="节目名 (そこ曲がったら櫻坂 / ちょこさく)")
    parser.add_argument("--all", action="store_true", help="下载所有节目")
    parser.add_argument("--token", type=str, help="x-service-token (直接传入，无需 .token 文件)")
    args = parser.parse_args()

    config = load_config()
    token = args.token or load_token()
    if not token:
        log.error("无可用 token — 请用 --token 参数传入或创建 .token 文件")
        sys.exit(1)

    log.info(f"Token: {token[:8]}...")

    # 从 state 获取最新 CRID
    state_path = SCRIPT_DIR / "watcher_state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}

    shows_to_process = []
    if args.all:
        for show in config.get("shows", []):
            name = show["name"]
            crid = state.get("shows", {}).get(name, {}).get("last_crid")
            if crid:
                shows_to_process.append((name, crid))
            else:
                log.warning(f"{name}: state 中无 last_crid，跳过")
    elif args.show:
        crid = state.get("shows", {}).get(args.show, {}).get("last_crid")
        if crid:
            shows_to_process.append((args.show, crid))
        else:
            log.error(f"{args.show}: state 中无 last_crid")
            sys.exit(1)
    else:
        parser.print_help()
        sys.exit(1)

    dl_token = token  # same token for API queries and downloads
    for show_name, start_crid in shows_to_process:
        batch_download_show(show_name, start_crid, config, token, dl_token)
        time.sleep(5)


if __name__ == "__main__":
    main()
