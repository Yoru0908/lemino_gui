#!/usr/bin/env python3
"""
Lemino Playwright Download - 用浏览器会话拦截 watch/ready 获取 playback info，
再调 lemino_dl.py direct mode 下载。

解决 DELW000016 sale type error：浏览器带完整 cookies/session 调 watch/ready 成功，
而纯 API 调用只带 x-service-token 会被拒。

用法:
    python3 playwright_download.py <CID_or_URL> [--quality 1080] [-o output.mp4]
    python3 playwright_download.py 00mm8i4y4p --quality 1080 -o /tmp/281.mp4
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
TOKEN_FILE = SCRIPT_DIR / ".token"
CRED_FILE = SCRIPT_DIR / ".credentials"
LEMINO_DL = SCRIPT_DIR / "lemino_dl.py"


def load_credentials():
    if CRED_FILE.exists():
        data = json.loads(CRED_FILE.read_text())
        return data.get("email"), data.get("password")
    return None, None


def get_playback_via_browser(cid: str, headless: bool = True) -> dict:
    """
    1. Playwright 登录 Lemino
    2. 导航到集数页面
    3. 拦截 watch/ready 响应
    4. 返回 {mpd_url, la_url, custom_data}
    """
    from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout

    email, password = load_credentials()
    if not email or not password:
        print("[!] No credentials found. Run auto_login.py first.")
        return {}

    result = {}

    def on_response(resp):
        if "watch/ready" in resp.url and resp.status == 200:
            try:
                body = resp.json()
                if body.get("result") == "0":
                    pl = (body.get("play_list") or [{}])[0]
                    result["mpd_url"] = pl.get("play_url", "")
                    result["la_url"] = pl.get("la_url", "")
                    result["custom_data"] = pl.get("custom_data", "")
                    result["play_token"] = body.get("play_token", "")
                    print(f"[+] watch/ready SUCCESS!")
                    print(f"    MPD: {result['mpd_url'][:80]}...")
                else:
                    code = body.get("result_code", "?")
                    print(f"[-] watch/ready failed: {code}")
            except Exception as e:
                print(f"[-] watch/ready parse error: {e}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, channel="chrome")
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 720},
            locale="ja-JP",
        )
        page = context.new_page()
        page.on("response", on_response)

        try:
            # ── Login ──
            print("[1/4] Loading Lemino...")
            page.goto("https://lemino.docomo.ne.jp/", wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(2000)

            # Check if already logged in (token in localStorage)
            existing_token = page.evaluate("localStorage.getItem('X-Service-Token')")

            if not existing_token:
                # Consent
                print("[2/4] Login required...")
                try:
                    btn = page.locator('text="規約に同意して利用を開始"')
                    if btn.is_visible(timeout=3000):
                        btn.click()
                        page.wait_for_timeout(2000)
                except PwTimeout:
                    pass

                # OAuth
                try:
                    page.get_by_text("dアカウントをお持ちの方").first.click(timeout=8000)
                    page.wait_for_load_state("networkidle", timeout=15000)
                except Exception:
                    pass

                # d-account login
                try:
                    page.wait_for_selector('input[type="text"], input[type="email"]', timeout=10000)
                    page.locator('input[type="text"], input[type="email"]').first.fill(email)
                    try:
                        page.get_by_text("次へ", exact=True).first.click()
                    except Exception:
                        page.locator('button[type="submit"]').first.click()

                    page.wait_for_selector('input[type="password"]', timeout=10000)
                    page.locator('input[type="password"]').first.fill(password)
                    try:
                        page.get_by_text("ログイン", exact=True).first.click()
                    except Exception:
                        page.locator('button[type="submit"]').first.click()
                except PwTimeout as e:
                    print(f"[!] Login form error: {e}")
                    browser.close()
                    return {}

                # Wait for redirect
                try:
                    page.wait_for_url("**/lemino.docomo.ne.jp/**", timeout=20000)
                except PwTimeout:
                    page.goto("https://lemino.docomo.ne.jp/", wait_until="networkidle", timeout=30000)

                page.wait_for_timeout(3000)
                token = page.evaluate("localStorage.getItem('X-Service-Token')")
                if token:
                    print(f"[+] Logged in. Token: {token[:8]}...")
                    TOKEN_FILE.write_text(json.dumps({"x-service-token": token}))
                else:
                    print("[!] Login failed - no token")
                    page.screenshot(path="/tmp/lemino_pw_dl_debug.png")
                    browser.close()
                    return {}
            else:
                print(f"[2/4] Already logged in. Token: {existing_token[:8]}...")

            # ── Navigate to episode ──
            print(f"[3/4] Navigating to episode {cid}...")
            page.goto(
                f"https://lemino.docomo.ne.jp/contents/{cid}",
                wait_until="domcontentloaded",
                timeout=30000,
            )
            page.wait_for_timeout(5000)

            # Try clicking play if not auto-played
            if not result.get("mpd_url"):
                print("    Trying to click play button...")
                for selector in [
                    'button[class*="play"]',
                    '[class*="PlayButton"]',
                    '[aria-label*="再生"]',
                    'button:has-text("再生")',
                    '[class*="player"] button',
                ]:
                    try:
                        el = page.locator(selector).first
                        if el.is_visible(timeout=2000):
                            el.click()
                            print(f"    Clicked: {selector}")
                            break
                    except Exception:
                        continue

                # Wait for watch/ready response
                page.wait_for_timeout(8000)

            if result.get("mpd_url"):
                print(f"[4/4] Playback info captured!")
                # Also save updated token
                updated_token = page.evaluate("localStorage.getItem('X-Service-Token')")
                if updated_token:
                    TOKEN_FILE.write_text(json.dumps({"x-service-token": updated_token}))
            else:
                print("[!] No watch/ready response captured.")
                print(f"    URL: {page.url}")
                page.screenshot(path="/tmp/lemino_pw_dl_debug.png")
                print("    Screenshot: /tmp/lemino_pw_dl_debug.png")

        except Exception as e:
            print(f"[!] Error: {e}")
            try:
                page.screenshot(path="/tmp/lemino_pw_dl_error.png")
            except:
                pass
        finally:
            browser.close()

    return result


def main():
    parser = argparse.ArgumentParser(description="Lemino Playwright Download")
    parser.add_argument("target", help="CID (e.g. 00mm8i4y4p) or Lemino URL")
    parser.add_argument("--quality", type=int, default=1080)
    parser.add_argument("-o", "--output", help="Output file path")
    parser.add_argument("--headed", action="store_true", help="Show browser")
    args = parser.parse_args()

    # Extract CID from URL if needed
    cid = args.target
    if "lemino.docomo.ne.jp" in cid:
        import re
        m = re.search(r'/contents/([a-z0-9]+)', cid)
        if m:
            cid = m.group(1)
        else:
            print(f"[!] Cannot extract CID from URL: {cid}")
            sys.exit(1)

    print(f"=== Lemino Playwright Download ===")
    print(f"CID: {cid}  Quality: {args.quality}")
    print()

    # Step 1: Get playback info via browser
    info = get_playback_via_browser(cid, headless=not args.headed)
    if not info.get("mpd_url"):
        print("\n[!] Failed to get playback info. Try --headed to debug.")
        sys.exit(1)

    # Step 2: Download via lemino_dl.py direct mode
    print(f"\n=== Starting download (direct mode) ===")
    cmd = [
        sys.executable, str(LEMINO_DL),
        "--mpd", info["mpd_url"],
        "--la-url", info["la_url"],
        "--custom-data", info["custom_data"],
        "--quality", str(args.quality),
    ]
    if args.output:
        cmd += ["-o", args.output]

    print(f"Running: {' '.join(cmd[:6])}...")
    ret = subprocess.run(cmd)
    sys.exit(ret.returncode)


if __name__ == "__main__":
    main()
