#!/usr/bin/env python3
"""
Lemino GUI Downloader
Streamlit-based GUI for downloading DRM videos from Lemino.

Usage:
    streamlit run lemino_gui.py
"""

import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

import streamlit as st

SCRIPT_DIR = Path(__file__).parent
TOKEN_FILE = SCRIPT_DIR / ".token"
CRED_FILE = SCRIPT_DIR / ".credentials"


def pick_folder() -> str | None:
    """Open native folder picker via tkinter subprocess. Returns path or None."""
    try:
        result = subprocess.run(
            [
                sys.executable, "-c",
                "import tkinter as tk; from tkinter import filedialog; "
                "root = tk.Tk(); root.withdraw(); root.wm_attributes('-topmost', True); "
                "p = filedialog.askdirectory(); print(p) if p else None",
            ],
            capture_output=True, text=True, timeout=60,
        )
        path = result.stdout.strip()
        return path if path else None
    except Exception:
        return None

# ─── Page config ────────────────────────────────────────────

st.set_page_config(
    page_title="Lemino Downloader",
    page_icon="🎬",
    layout="centered",
)

st.markdown("""
<style>
.log-box {
    background: #0e1117;
    color: #a8ff78;
    font-family: 'Courier New', monospace;
    font-size: 0.82em;
    padding: 12px 16px;
    border-radius: 8px;
    max-height: 360px;
    overflow-y: auto;
    white-space: pre-wrap;
    word-break: break-all;
}
.status-ok  { color: #28a745; font-weight: bold; }
.status-err { color: #dc3545; font-weight: bold; }
.status-inf { color: #6c757d; }
</style>
""", unsafe_allow_html=True)


# ─── Session state helpers ──────────────────────────────────

def get_token() -> str | None:
    if TOKEN_FILE.exists():
        try:
            return json.loads(TOKEN_FILE.read_text()).get("x-service-token")
        except Exception:
            pass
    return None


def get_saved_credentials() -> tuple[str, str]:
    if CRED_FILE.exists():
        try:
            d = json.loads(CRED_FILE.read_text())
            return d.get("email", ""), d.get("password", "")
        except Exception:
            pass
    return "", ""


def token_display(token: str | None) -> str:
    if token:
        return f"✅ Token 已就绪 (`{token[:6]}...{token[-4:]}`)"
    return "❌ 尚未登录"


# ─── Background subprocess runner ──────────────────────────

def run_subprocess(cmd: list[str], log_queue: queue.Queue, cwd: str = None):
    """Run a subprocess and push output lines to queue. Push None when done."""
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=cwd or str(SCRIPT_DIR),
        )
        for line in proc.stdout:
            log_queue.put(line.rstrip())
        proc.wait()
        log_queue.put(("__returncode__", proc.returncode))
    except Exception as e:
        log_queue.put(f"[ERROR] {e}")
        log_queue.put(("__returncode__", 1))
    finally:
        log_queue.put(None)  # sentinel


# ─── Main UI ────────────────────────────────────────────────

st.title("🎬 Lemino Downloader")
st.caption("Lemino (docomo) DRM 视频下载工具")

st.divider()

# ── Section 1: Login ────────────────────────────────────────
st.subheader("🔑 账号登录")

token = get_token()
saved_email, saved_pass = get_saved_credentials()

col_status, col_clear = st.columns([5, 1])
with col_status:
    token_status = st.empty()
    token_status.markdown(token_display(token))
with col_clear:
    if token and st.button("清除", help="清除已保存的 token"):
        TOKEN_FILE.unlink(missing_ok=True)
        st.rerun()

with st.expander("📧 输入账号密码自动登录", expanded=(not token)):
    email_input = st.text_input(
        "d-account 邮箱",
        value=saved_email,
        placeholder="例: yourmail@example.com",
    )
    pass_input = st.text_input(
        "密码",
        value=saved_pass,
        type="password",
        placeholder="d-account 登录密码",
    )

    login_col1, login_col2 = st.columns([2, 3])
    with login_col1:
        do_login = st.button("🔑 登录获取 Token", use_container_width=True, type="primary")
    with login_col2:
        st.caption("首次登录后凭据会加密保存，下次自动填入")

    if do_login:
        if not email_input or not pass_input:
            st.error("请输入邮箱和密码")
        else:
            login_log = st.empty()
            login_lines = []
            log_q: queue.Queue = queue.Queue()

            t = threading.Thread(
                target=run_subprocess,
                args=(
                    [
                        sys.executable,
                        str(SCRIPT_DIR / "auto_login.py"),
                        "--email", email_input,
                        "--password", pass_input,
                    ],
                    log_q,
                ),
                daemon=True,
            )
            t.start()

            with st.spinner("正在通过 Playwright 登录 Lemino..."):
                success = None
                while True:
                    item = log_q.get()
                    if item is None:
                        break
                    if isinstance(item, tuple) and item[0] == "__returncode__":
                        success = (item[1] == 0)
                        continue
                    login_lines.append(item)
                    login_log.code("\n".join(login_lines[-20:]), language="text")

            if success:
                new_token = get_token()
                if new_token:
                    st.success(f"登录成功！Token: `{new_token[:6]}...`")
                    token_status.markdown(token_display(new_token))
                    st.rerun()
                else:
                    st.error("登录流程完成但未获取到 Token，请重试或手动输入 Token")
            else:
                st.error("登录失败，请检查账号密码")

with st.expander("🔧 手动输入 Token"):
    manual_token = st.text_input(
        "x-service-token",
        placeholder="从 Chrome Console: localStorage.getItem('X-Service-Token')",
        max_chars=32,
    )
    if st.button("保存 Token"):
        if manual_token and len(manual_token) == 32:
            TOKEN_FILE.write_text(json.dumps({"x-service-token": manual_token}))
            st.success("Token 已保存")
            st.rerun()
        else:
            st.error("Token 应为 32 位字符串")

st.divider()

# ── Section 2: Download ─────────────────────────────────────
st.subheader("⬇️ 下载视频")

token = get_token()
if not token:
    st.warning("请先在上方登录账号", icon="⚠️")

url_input = st.text_input(
    "Lemino 视频 URL",
    placeholder="https://lemino.docomo.ne.jp/?crid=... 或 https://lemino.docomo.ne.jp/contents/...",
    disabled=(not token),
)

dl_col1, dl_col2, dl_col3 = st.columns([2, 2, 3])
with dl_col1:
    quality = st.selectbox(
        "画质",
        options=[1080, 720, 540, 480, 180],
        index=0,
        disabled=(not token),
    )
with dl_col2:
    output_name = st.text_input(
        "输出文件名",
        placeholder="output.mp4  (留空自动命名)",
        disabled=(not token),
    )
with dl_col3:
    if "output_dir" not in st.session_state:
        st.session_state["output_dir"] = str(Path.home() / "Downloads")

    dir_label_col, dir_btn_col = st.columns([5, 2])
    with dir_label_col:
        output_dir = st.text_input(
            "保存到目录",
            value=st.session_state["output_dir"],
            key="output_dir_input",
            disabled=(not token),
        )
        st.session_state["output_dir"] = output_dir
    with dir_btn_col:
        st.write("")
        st.write("")
        if st.button("📁 浏览", disabled=(not token), use_container_width=True):
            chosen = pick_folder()
            if chosen:
                st.session_state["output_dir"] = chosen
                st.rerun()

do_download = st.button(
    "▶ 开始下载",
    use_container_width=True,
    type="primary",
    disabled=(not token or not url_input),
)

# ── Download log area ────────────────────────────────────────
log_placeholder = st.empty()

if do_download and token and url_input:
    # Build command
    cmd = [sys.executable, str(SCRIPT_DIR / "lemino_dl.py")]
    cmd.append(url_input.strip())
    cmd += ["--quality", str(quality)]

    out_dir = Path(output_dir.strip()) if output_dir.strip() else Path.home() / "Downloads"
    out_dir.mkdir(parents=True, exist_ok=True)

    if output_name.strip():
        fname = output_name.strip()
        if not fname.endswith(".mp4"):
            fname += ".mp4"
        out_path = out_dir / fname
        cmd += ["-o", str(out_path)]
        run_cwd = str(SCRIPT_DIR)
    else:
        # No explicit name: set cwd to out_dir so auto-named file lands there
        run_cwd = str(out_dir)

    dl_lines = []
    dl_q: queue.Queue = queue.Queue()

    t = threading.Thread(
        target=run_subprocess,
        args=(cmd, dl_q),
        kwargs={"cwd": run_cwd},
        daemon=True,
    )
    t.start()

    progress_bar = st.progress(0, text="准备中...")
    log_box = st.empty()

    step_map = {
        "[1/5]": (10, "获取播放信息..."),
        "[2/5]": (30, "解析 MPD 清单..."),
        "[3/5]": (50, "获取 Widevine 解密密钥..."),
        "[4/5]": (70, "下载加密片段..."),
        "[5/5]": (90, "合并视频..."),
        "=== DONE": (100, "✅ 下载完成！"),
    }
    final_success = None

    while True:
        item = dl_q.get()
        if item is None:
            break
        if isinstance(item, tuple) and item[0] == "__returncode__":
            final_success = (item[1] == 0)
            continue

        dl_lines.append(item)

        # Update progress bar based on step markers
        for marker, (pct, label) in step_map.items():
            if marker in item:
                progress_bar.progress(pct / 100, text=label)
                break

        # Render last N lines
        log_html = "\n".join(dl_lines[-60:])
        log_box.markdown(
            f'<div class="log-box">{log_html}</div>',
            unsafe_allow_html=True,
        )

    if final_success:
        progress_bar.progress(1.0, text="✅ 下载完成！")
        st.success(f"下载完成！文件已保存到: `{output_dir}`")
        st.balloons()
    else:
        progress_bar.progress(1.0, text="❌ 下载失败")
        st.error("下载失败，请查看上方日志了解详情")

elif not do_download:
    log_placeholder.markdown(
        '<div class="log-box" style="color:#555;min-height:80px;">等待下载...</div>',
        unsafe_allow_html=True,
    )

st.divider()

# ── Section 3: Help ──────────────────────────────────────────
with st.expander("📖 使用说明"):
    st.markdown("""
**首次使用步骤：**

1. 在「账号登录」区域输入你的 **d-account 邮箱和密码**，点击登录
2. 登录成功后 Token 会自动保存，以后不需要再登录
3. 在 Lemino 网页找到要下载的视频，**复制浏览器地址栏的 URL**
4. 粘贴到「Lemino 视频 URL」输入框，点击开始下载

**获取 URL 方式：**
- 打开 `https://lemino.docomo.ne.jp` 找到视频
- 复制地址栏 URL（支持 `?crid=...` 格式和 `/contents/...` 格式）

**Token 过期处理：**
- 点击「清除」按钮，重新登录即可

**系统环境要求（仅首次配置）：**
```bash
# macOS
brew install bento4 ffmpeg
pip3 install pywidevine requests streamlit playwright
playwright install chromium
```
""")
