from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TypedDict
from urllib.parse import quote

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from scraper.disposable_inbox import create_inbox_for_email, supports_auto_email_code
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


def _clerk_sign_in_phase1(page: Page, *, email: str, password: str) -> dict:
    return page.evaluate(
        """async ({ email, password }) => {
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

          window.__macroPulsePendingSignIn = signIn;
          return {
            status: 'needs_email_code',
            safeIdentifier: emailFactor.safeIdentifier ?? null,
          };
        }""",
        {"email": email, "password": password},
    )


def _clerk_sign_in_phase2(page: Page, *, email_code: str) -> dict:
    return page.evaluate(
        """async ({ emailCode }) => {
          const signIn = window.__macroPulsePendingSignIn;
          if (!signIn) {
            return { status: 'error', error: 'No pending Clerk sign-in attempt' };
          }

          const second = await signIn.attemptSecondFactor({
            strategy: 'email_code',
            code: emailCode,
          });

          delete window.__macroPulsePendingSignIn;

          if (second.status === 'complete' && second.createdSessionId) {
            await window.Clerk.setActive({ session: second.createdSessionId });
            return { status: 'complete', sessionId: second.createdSessionId };
          }

          return {
            status: second.status,
            supportedSecondFactors: signIn.supportedSecondFactors ?? [],
          };
        }""",
        {"emailCode": email_code},
    )


def _resolve_email_code(email: str, manual_code: str | None) -> str | None:
    if manual_code:
        return manual_code

    env_code = os.getenv("PULSE_MFA_CODE", "").strip()
    if env_code:
        return env_code

    return None


def _fetch_email_code_from_inbox(email: str) -> str:
    try:
        inbox = create_inbox_for_email(email)
    except ValueError as exc:
        raise ClerkLoginError(str(exc)) from exc

    timeout = float(os.getenv("PULSE_EMAIL_CODE_TIMEOUT", "120"))
    poll_interval = float(os.getenv("PULSE_EMAIL_POLL_INTERVAL", "3"))

    try:
        return inbox.wait_for_verification_code_sync(
            timeout_seconds=timeout,
            poll_interval_seconds=poll_interval,
        )
    except TimeoutError as exc:
        raise ClerkLoginError(
            f"Timed out waiting for Clerk verification code in inbox for {email}. "
            "Ensure your MacroPulse account uses this disposable address."
        ) from exc
    except RuntimeError as exc:
        raise ClerkLoginError(f"Failed to read disposable inbox for {email}: {exc}") from exc


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

    sign_in_url = _sign_in_url(base_url=base_url)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()

        try:
            page.goto(sign_in_url, wait_until="networkidle", timeout=timeout_ms)
            page.wait_for_function("window.Clerk && window.Clerk.loaded", timeout=timeout_ms)

            result = _clerk_sign_in_phase1(page, email=email, password=password)

            if result.get("status") == "needs_email_code":
                resolved_code = _resolve_email_code(email, email_code)
                if not resolved_code and supports_auto_email_code(email):
                    resolved_code = _fetch_email_code_from_inbox(email)

                if not resolved_code:
                    identifier = result.get("safeIdentifier") or email
                    raise ClerkLoginError(
                        "Clerk requires an email verification code after password sign-in. "
                        f"Check {identifier} for the code and set PULSE_MFA_CODE, "
                        "register the account with a supported disposable email "
                        "(1secmail, mail.tm, guerrillamail), or set CLERK_SESSION "
                        "from a completed browser login for unattended sync."
                    )

                result = _clerk_sign_in_phase2(page, email_code=resolved_code)

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
