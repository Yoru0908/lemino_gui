#!/usr/bin/env python3
"""
Lemino Auto Login - Playwright headless browser
自动登录 d-account 获取 x-service-token

用法:
    python3 auto_login.py                    # 使用保存的凭据
    python3 auto_login.py --email X --pass Y # 指定凭据
"""
import json
import sys
import argparse
from pathlib import Path

TOKEN_FILE = Path(__file__).parent / ".token"
CRED_FILE = Path(__file__).parent / ".credentials"

DEFAULT_EMAIL = ""
DEFAULT_PASS = ""


def save_token(token: str):
    TOKEN_FILE.write_text(json.dumps({"x-service-token": token}))
    print(f"[+] Token saved: {token[:8]}...")


def save_credentials(email: str, password: str):
    CRED_FILE.write_text(json.dumps({"email": email, "password": password}))


def load_credentials():
    if CRED_FILE.exists():
        data = json.loads(CRED_FILE.read_text())
        return data.get("email"), data.get("password")
    return DEFAULT_EMAIL or None, DEFAULT_PASS or None


def _wait_for_token(page, max_wait_ms: int = 20000, interval_ms: int = 1000):
    """Poll localStorage until X-Service-Token appears or timeout."""
    import time
    deadline = time.time() + max_wait_ms / 1000
    keys = ['X-Service-Token', 'x-service-token', 'X-SERVICE-TOKEN']
    while time.time() < deadline:
        for k in keys:
            try:
                val = page.evaluate(f"localStorage.getItem('{k}')")
                if val:
                    return val
            except Exception:
                pass
        time.sleep(interval_ms / 1000)
    return None


def _do_daccount_login(login_page, email: str, password: str):
    """Fill d-account login form (email → next → password → login)."""
    from playwright.sync_api import TimeoutError as PwTimeout

    # Wait for email input to be visible
    try:
        login_page.wait_for_selector('input[type="text"], input[type="email"]', timeout=10000)
    except PwTimeout:
        print("    [!] Email field not found")
        return

    login_page.locator('input[type="text"], input[type="email"]').first.fill(email)
    # Try clicking 次へ by text or by submit button
    try:
        login_page.get_by_text("次へ", exact=True).first.click()
    except Exception:
        login_page.locator('button[type="submit"], input[type="submit"]').first.click()
    print("    Email entered, clicking next...")

    # Wait for password field
    try:
        login_page.wait_for_selector('input[type="password"]', timeout=10000)
    except PwTimeout:
        print("    [!] Password field not found")
        return

    login_page.locator('input[type="password"]').first.fill(password)
    try:
        login_page.get_by_text("ログイン", exact=True).first.click()
    except Exception:
        login_page.locator('button[type="submit"], input[type="submit"]').first.click()
    print("    Password entered, logging in...")


def auto_login(email: str, password: str, headless: bool = True) -> str:
    """
    自动登录 Lemino via Lemino's OAuth flow, 返回 authenticated x-service-token.

    流程:
    1. Lemino 首页 → 同意规约
    2. 点击「dアカウントをお持ちの方」→ 跳转 d-account OAuth
    3. d-account 登录 (email + password)
    4. OAuth 回调回 Lemino → authenticated session
    5. 从 localStorage 读取 token
    """
    from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout

    print(f"[*] Starting {'headless' if headless else 'headed'} browser...")
    print(f"[*] Email: {email}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, channel="chrome")
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 720},
            locale="ja-JP",
        )
        page = context.new_page()

        try:
            # Step 1: Load Lemino homepage
            print("[1/5] Loading Lemino...")
            page.goto("https://lemino.docomo.ne.jp/", wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(2000)

            # Step 2: Accept consent dialog if present
            print("[2/5] Checking consent dialog...")
            try:
                btn = page.locator('text="規約に同意して利用を開始"')
                if btn.is_visible(timeout=5000):
                    btn.click()
                    print("    Consent accepted")
                    page.wait_for_timeout(2000)
            except PwTimeout:
                print("    No consent dialog")

            # Step 3: Click "dアカウントをお持ちの方" to start OAuth
            print("[3/5] Starting OAuth via dアカウントをお持ちの方...")
            try:
                btn3 = page.get_by_text("dアカウントをお持ちの方")
                btn3.first.click(timeout=8000)
            except Exception as e3:
                print(f"    [!] OAuth button not found: {e3}")
            # Wait for navigation to d-account
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except PwTimeout:
                pass
            print(f"    URL: {page.url[:80]}...")

            # Step 4: d-account login
            print(f"[4/5] d-account login...")
            _do_daccount_login(page, email, password)
            print(f"    After login: {page.url[:80]}...")

            # Step 5: Wait for redirect back to Lemino
            print("[5/5] Waiting for Lemino redirect...")
            try:
                page.wait_for_url("**/lemino.docomo.ne.jp/**", timeout=15000)
            except PwTimeout:
                page.goto("https://lemino.docomo.ne.jp/", wait_until="networkidle", timeout=30000)

            # Poll localStorage for up to 25 seconds
            print("[5/5] Waiting for token in localStorage...")
            token = _wait_for_token(page, max_wait_ms=25000)
            if token:
                print(f"\n[+] SUCCESS! Token: {token[:8]}...")
                save_token(token)
                save_credentials(email, password)
                browser.close()
                return token

            page.screenshot(path="/tmp/lemino_token_debug.png")
            print(f"[!] No token after 25s. URL: {page.url}")
            print(f"    Screenshot: /tmp/lemino_token_debug.png")
            browser.close()
            return None

        except Exception as e:
            try:
                page.screenshot(path="/tmp/lemino_error_debug.png")
            except:
                pass
            print(f"[!] Error: {e}")
            browser.close()
            return None


def main():
    parser = argparse.ArgumentParser(description="Lemino Auto Login")
    parser.add_argument("--email", help="d-account email")
    parser.add_argument("--password", "--pass", dest="password", help="d-account password")
    parser.add_argument("--headed", action="store_true", help="Show browser window")
    args = parser.parse_args()

    email = args.email
    password = args.password
    if not email or not password:
        saved_email, saved_pass = load_credentials()
        email = email or saved_email
        password = password or saved_pass

    token = auto_login(email, password, headless=not args.headed)
    if token:
        print(f"\nToken: {token}")
        sys.exit(0)
    else:
        print("\nLogin failed.")
        sys.exit(1)


if __name__ == "__main__":
    main()
