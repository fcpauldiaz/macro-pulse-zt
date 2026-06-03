from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TypedDict
from urllib.parse import quote

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from scraper.errors import ClerkLoginError

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


def _wait_for_session_cookie(page: Page, timeout_ms: int) -> None:
    page.wait_for_function(
        """() => document.cookie.includes('__session=')""",
        timeout=timeout_ms,
    )


def _clerk_sign_in(page: Page, *, email: str, password: str, email_code: str | None) -> dict:
    return page.evaluate(
        """async ({ email, password, emailCode }) => {
          const signIn = await window.Clerk.client.signIn.create({ identifier: email });
          const first = await signIn.attemptFirstFactor({ strategy: 'password', password });

          if (first.status === 'complete' && first.createdSessionId) {
            await window.Clerk.setActive({ session: first.createdSessionId });
            return { status: 'complete', sessionId: first.createdSessionId };
          }

          if (first.status !== 'needs_second_factor') {
            return {
              status: first.status,
              supportedSecondFactors: signIn.supportedSecondFactors ?? [],
            };
          }

          const factors = signIn.supportedSecondFactors ?? [];
          const emailFactor = factors.find((factor) => factor.strategy === 'email_code');
          if (!emailFactor) {
            return {
              status: first.status,
              supportedSecondFactors: factors,
              error: 'Unsupported second factor',
            };
          }

          await signIn.prepareSecondFactor({
            strategy: 'email_code',
            emailAddressId: emailFactor.emailAddressId,
          });

          if (!emailCode) {
            return {
              status: 'needs_email_code',
              safeIdentifier: emailFactor.safeIdentifier ?? null,
            };
          }

          const second = await signIn.attemptSecondFactor({
            strategy: 'email_code',
            code: emailCode,
          });

          if (second.status === 'complete' && second.createdSessionId) {
            await window.Clerk.setActive({ session: second.createdSessionId });
            return { status: 'complete', sessionId: second.createdSessionId };
          }

          return { status: second.status, supportedSecondFactors: factors };
        }""",
        {"email": email, "password": password, "emailCode": email_code},
    )


def login_and_save_session(
    *,
    email: str,
    password: str,
    session_path: Path = DEFAULT_SESSION_PATH,
    base_url: str = BASE_URL,
    headless: bool = True,
    timeout_ms: int = 90_000,
    email_code: str | None = None,
) -> dict[str, str]:
    if not email.strip():
        raise ClerkLoginError("PULSE_EMAIL must not be empty")
    if not password:
        raise ClerkLoginError("PULSE_PASSWORD must not be empty")

    resolved_email_code = email_code or os.getenv("PULSE_MFA_CODE", "").strip() or None
    sign_in_url = _sign_in_url(base_url=base_url)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()

        try:
            page.goto(sign_in_url, wait_until="networkidle", timeout=timeout_ms)
            page.wait_for_function("window.Clerk && window.Clerk.loaded", timeout=timeout_ms)

            result = _clerk_sign_in(
                page,
                email=email,
                password=password,
                email_code=resolved_email_code,
            )

            if result.get("status") == "needs_email_code":
                identifier = result.get("safeIdentifier") or "your email"
                raise ClerkLoginError(
                    "Clerk requires an email verification code after password sign-in. "
                    f"Check {identifier} for the code and set PULSE_MFA_CODE for this run, "
                    "or set CLERK_SESSION from a completed browser login for unattended sync."
                )

            if result.get("status") != "complete":
                raise ClerkLoginError(
                    "Clerk sign-in did not complete "
                    f"(status={result.get('status')}, factors={result.get('supportedSecondFactors')})."
                )

            _wait_for_session_cookie(page, timeout_ms)
            page.goto(f"{base_url.rstrip('/')}{PULSE_PATH}", wait_until="networkidle", timeout=timeout_ms)
        except PlaywrightTimeoutError as exc:
            raise ClerkLoginError(
                "Timed out waiting for Clerk sign-in; verify credentials, MFA, or email code settings"
            ) from exc
        finally:
            cookies = context.cookies()
            browser.close()

    if not any(cookie.get("name") == "__session" for cookie in cookies):
        raise ClerkLoginError("Login completed but '__session' cookie was not set")

    save_session_cookies(cookies, session_path)
    return load_session_cookies(session_path)
