from __future__ import annotations

import json
from pathlib import Path
from typing import TypedDict
from urllib.parse import quote

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

BASE_URL = "https://macro-wrap.vercel.app"
DEFAULT_SESSION_PATH = Path(".pulse_session.json")
SIGN_IN_PATH = "/sign-in"
PULSE_PATH = "/pulse"


class StoredCookie(TypedDict, total=False):
    name: str
    value: str
    domain: str
    path: str
    expires: float
    httpOnly: bool
    secure: bool
    sameSite: str


class ClerkLoginError(Exception):
    pass


def _sign_in_url(*, redirect_path: str = PULSE_PATH, base_url: str = BASE_URL) -> str:
    redirect_url = quote(f"{base_url.rstrip('/')}{redirect_path}", safe="")
    return f"{base_url.rstrip('/')}{SIGN_IN_PATH}?redirect_url={redirect_url}"


def _serialize_cookies(cookies: list[StoredCookie]) -> list[StoredCookie]:
    return [
        {
            "name": cookie["name"],
            "value": cookie["value"],
            "domain": cookie.get("domain", ""),
            "path": cookie.get("path", "/"),
            "expires": cookie.get("expires", -1),
            "httpOnly": cookie.get("httpOnly", False),
            "secure": cookie.get("secure", False),
            "sameSite": cookie.get("sameSite", "Lax"),
        }
        for cookie in cookies
    ]


def save_session_cookies(cookies: list[StoredCookie], session_path: Path) -> None:
    session_path.parent.mkdir(parents=True, exist_ok=True)
    session_path.write_text(json.dumps(_serialize_cookies(cookies), indent=2), encoding="utf-8")


def load_session_cookies(session_path: Path) -> dict[str, str]:
    if not session_path.exists():
        raise ClerkLoginError(f"Session file not found: {session_path}")

    raw = json.loads(session_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ClerkLoginError("Session file must contain a JSON array of cookies")

    cookies: dict[str, str] = {}
    for item in raw:
        if isinstance(item, dict) and item.get("name") and item.get("value") is not None:
            cookies[str(item["name"])] = str(item["value"])

    if "__session" not in cookies:
        raise ClerkLoginError("Session file is missing required '__session' cookie")

    return cookies


def login_and_save_session(
    *,
    email: str,
    password: str,
    session_path: Path = DEFAULT_SESSION_PATH,
    base_url: str = BASE_URL,
    headless: bool = True,
    timeout_ms: int = 60_000,
) -> dict[str, str]:
    if not email.strip():
        raise ClerkLoginError("PULSE_EMAIL must not be empty")
    if not password:
        raise ClerkLoginError("PULSE_PASSWORD must not be empty")

    sign_in_url = _sign_in_url(base_url=base_url)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()

        try:
            page.goto(sign_in_url, wait_until="domcontentloaded", timeout=timeout_ms)

            email_input = page.locator('input[name="identifier"], input[type="email"]').first
            email_input.wait_for(state="visible", timeout=timeout_ms)
            email_input.fill(email)

            continue_button = page.get_by_role("button", name="Continue", exact=True)
            if continue_button.count() > 0:
                continue_button.click()
            else:
                page.locator('button[type="submit"]').first.click()

            password_input = page.locator('input[name="password"], input[type="password"]').first
            password_input.wait_for(state="visible", timeout=timeout_ms)
            password_input.fill(password)

            submit_button = page.locator('button[type="submit"]').first
            submit_button.click()

            page.wait_for_url(f"**{PULSE_PATH}**", timeout=timeout_ms)
        except PlaywrightTimeoutError as exc:
            raise ClerkLoginError(
                "Timed out waiting for Clerk sign-in; verify credentials and MFA settings"
            ) from exc
        finally:
            cookies = context.cookies()
            browser.close()

    if not any(cookie.get("name") == "__session" for cookie in cookies):
        raise ClerkLoginError("Login completed but '__session' cookie was not set")

    save_session_cookies(cookies, session_path)
    return load_session_cookies(session_path)
