#!/usr/bin/env python3
"""
Intercept the actual watch/ready request from Lemino browser to capture
cookies and exact payload for use in lemino_dl.py
"""
import json, sys
from pathlib import Path
from playwright.sync_api import sync_playwright

EPISODE_URL = "https://lemino.docomo.ne.jp/contents/00mm8i4y4p"
TOKEN_FILE = Path(__file__).parent / ".token"
CRED_FILE  = Path(__file__).parent / ".credentials"
OUT_FILE   = Path("/tmp/lemino_watch_intercept.json")

captured = {}

def main():
    creds = {}
    if CRED_FILE.exists():
        creds = json.loads(CRED_FILE.read_text())

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, channel="chrome")
        ctx = browser.new_context()

        # Load saved cookies/storage if token exists
        if TOKEN_FILE.exists():
            raw = TOKEN_FILE.read_text().strip()
            token = json.loads(raw)["x-service-token"] if raw.startswith("{") else raw
            ctx.add_init_script(f"""
                Object.defineProperty(window, '__playwright_token', {{value: '{token}'}});
                localStorage.setItem('X-Service-Token', '{token}');
            """)

        page = ctx.new_page()

        def on_request(req):
            if "watch/ready" in req.url and req.method == "POST":
                print(f"[+] Intercepted watch/ready request!")
                try:
                    captured["url"] = req.url
                    captured["headers"] = dict(req.headers)
                    captured["post_data"] = req.post_data
                    captured["cookies"] = {c["name"]: c["value"] for c in ctx.cookies()}
                except Exception as e:
                    print(f"  Error: {e}")

        def on_response(resp):
            if "watch/ready" in resp.url:
                try:
                    body = resp.body()
                    data = json.loads(body)
                    captured["response"] = data
                    print(f"[+] Response: result={data.get('result')} result_code={data.get('result_code','OK')}")
                    if data.get("result") == "0":
                        play_list = data.get("play_list", [{}])
                        if play_list:
                            captured["mpd_url"] = play_list[0].get("play_url")
                            captured["la_url"] = play_list[0].get("la_url")
                            captured["custom_data"] = play_list[0].get("custom_data")
                            print(f"[+] MPD: {captured.get('mpd_url','')[:80]}")
                except Exception as e:
                    print(f"  Response parse error: {e}")

        page.on("request", on_request)
        page.on("response", on_response)

        print(f"[*] Navigating to episode page...")
        page.goto(EPISODE_URL, wait_until="domcontentloaded")
        print(f"[*] Page loaded. Waiting for play button or auto-play...")
        print(f"[*] If login required, please log in manually.")

        # Wait up to 30s for watch/ready to be called
        page.wait_for_timeout(5000)

        # Try clicking play button
        try:
            play_btn = page.query_selector('button[class*="play"], [aria-label*="play"], [class*="PlayButton"]')
            if play_btn:
                print("[*] Clicking play button...")
                play_btn.click()
        except Exception:
            pass

        page.wait_for_timeout(10000)

        if captured.get("response"):
            OUT_FILE.write_text(json.dumps(captured, ensure_ascii=False, indent=2))
            print(f"\n[+] Saved to {OUT_FILE}")
            if captured.get("mpd_url"):
                print(f"\n=== Direct mode command ===")
                print(f"python3 lemino_dl.py --mpd '{captured['mpd_url']}' --la-url '{captured.get('la_url','')}' --custom-data '{captured.get('custom_data','')[:40]}...' -o /tmp/281.mp4")
        else:
            print("\n[-] No watch/ready request captured. Make sure you click play in the browser window.")
            # Save cookies at minimum
            cookies = {c["name"]: c["value"] for c in ctx.cookies()}
            OUT_FILE.write_text(json.dumps({"cookies": cookies, "headers_tried": captured.get("headers", {})}, indent=2))
            print(f"[*] Saved cookies to {OUT_FILE}")

        browser.close()

if __name__ == "__main__":
    main()
