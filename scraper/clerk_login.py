from __future__ import annotations

import os
from pathlib import Path
from typing import TypedDict
from urllib.parse import quote

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from scraper.disposable_inbox import (
    InboxCredentials,
    generate_macro_pulse_password,
    inbox_credentials_path,
    load_saved_inbox_credentials,
    save_inbox_credentials,
    supports_auto_email_code,
)
from scraper.errors import ClerkLoginError
from scraper.mail_tm_browser import (
    open_mail_tm,
    read_email_address,
    save_storage_state,
    storage_state_path,
    wait_for_verification_code,
)

BASE_URL = "https://macro-wrap.vercel.app"
SIGN_IN_PATH = "/sign-in"
PULSE_PATH = "/pulse"

CLERK_SIGN_IN_JS = """async ({ email, password }) => {
  try {
    const signIn = await window.Clerk.client.signIn.create({ identifier: email, password });

    if (signIn.status === 'complete' && signIn.createdSessionId) {
      await window.Clerk.setActive({ session: signIn.createdSessionId });
      return { status: 'complete', sessionId: signIn.createdSessionId };
    }

    const needsEmailCode =
      signIn.status === 'needs_second_factor' || signIn.status === 'needs_client_trust';

    if (needsEmailCode) {
      const factors = signIn.supportedSecondFactors ?? [];
      const emailFactor = factors.find((factor) => factor.strategy === 'email_code');
      if (!emailFactor) {
        return {
          status: signIn.status,
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
    }

    return {
      status: signIn.status,
      supportedSecondFactors: signIn.supportedSecondFactors ?? [],
    };
  } catch (err) {
    const clerkError = err?.errors?.[0];
    return {
      status: 'error',
      code: clerkError?.code ?? 'clerk_error',
      message: clerkError?.longMessage || clerkError?.message || err?.message || String(err),
      supportedSecondFactors: [],
    };
  }
}"""

CLERK_SIGN_IN_PHASE2_JS = """async ({ emailCode }) => {
  try {
    const signIn = window.__macroPulsePendingSignIn;
    if (!signIn) {
      return { status: 'error', code: 'missing_sign_in', message: 'No pending Clerk sign-in attempt' };
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
  } catch (err) {
    const clerkError = err?.errors?.[0];
    return {
      status: 'error',
      code: clerkError?.code ?? 'clerk_error',
      message: clerkError?.longMessage || clerkError?.message || err?.message || String(err),
    };
  }
}"""

CLERK_TICKET_SIGN_IN_JS = """async ({ ticket }) => {
  try {
    const signIn = await window.Clerk.client.signIn.create({ strategy: 'ticket', ticket });
    if (signIn.status === 'complete' && signIn.createdSessionId) {
      await window.Clerk.setActive({ session: signIn.createdSessionId });
      return { status: 'complete', sessionId: signIn.createdSessionId };
    }
    return { status: signIn.status };
  } catch (err) {
    const clerkError = err?.errors?.[0];
    return {
      status: 'error',
      code: clerkError?.code ?? 'clerk_error',
      message: clerkError?.longMessage || clerkError?.message || err?.message || String(err),
    };
  }
}"""

CLERK_SIGN_UP_JS = """async ({ email, password }) => {
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  const readCaptchaToken = () => {
    const turnstile = document.querySelector('input[name="cf-turnstile-response"]');
    if (turnstile?.value) {
      return turnstile.value;
    }
    const recaptcha = document.querySelector('textarea[name="g-recaptcha-response"]');
    if (recaptcha?.value) {
      return recaptcha.value;
    }
    return null;
  };

  const waitForCaptchaToken = async (timeoutMs = 20000) => {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const token = readCaptchaToken();
      if (token) {
        return token;
      }
      await sleep(500);
    }
    return null;
  };

  try {
    const captchaToken = await waitForCaptchaToken();
    const payload = { emailAddress: email, password };
    if (captchaToken) {
      payload.captchaToken = captchaToken;
    }

    const signUp = await window.Clerk.client.signUp.create(payload);

    if (signUp.status === 'complete' && signUp.createdSessionId) {
      await window.Clerk.setActive({ session: signUp.createdSessionId });
      return { status: 'complete', sessionId: signUp.createdSessionId };
    }

    const needsEmailVerification =
      signUp.status === 'missing_requirements' &&
      (signUp.unverifiedFields ?? []).includes('email_address');

    if (needsEmailVerification) {
      await signUp.prepareEmailAddressVerification({ strategy: 'email_code' });
      window.__macroPulsePendingSignUp = signUp;
      return { status: 'needs_email_code' };
    }

    return {
      status: signUp.status,
      unverifiedFields: signUp.unverifiedFields ?? [],
      missingFields: signUp.missingFields ?? [],
    };
  } catch (err) {
    const clerkError = err?.errors?.[0];
    return {
      status: 'error',
      code: clerkError?.code ?? 'clerk_error',
      message: clerkError?.longMessage || clerkError?.message || err?.message || String(err),
      unverifiedFields: [],
      missingFields: [],
    };
  }
}"""

CLERK_SIGN_UP_VERIFY_JS = """async ({ emailCode }) => {
  try {
    const signUp = window.__macroPulsePendingSignUp;
    if (!signUp) {
      return { status: 'error', code: 'missing_sign_up', message: 'No pending Clerk sign-up attempt' };
    }

    const verified = await signUp.attemptEmailAddressVerification({ code: emailCode });
    delete window.__macroPulsePendingSignUp;

    if (verified.status === 'complete' && verified.createdSessionId) {
      await window.Clerk.setActive({ session: verified.createdSessionId });
      return { status: 'complete', sessionId: verified.createdSessionId };
    }

    return {
      status: verified.status,
      unverifiedFields: verified.unverifiedFields ?? [],
    };
  } catch (err) {
    const clerkError = err?.errors?.[0];
    return {
      status: 'error',
      code: clerkError?.code ?? 'clerk_error',
      message: clerkError?.longMessage || clerkError?.message || err?.message || String(err),
    };
  }
}"""


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


def _cookies_from_browser(cookies: list[StoredCookie]) -> dict[str, str]:
    result: dict[str, str] = {}
    for cookie in cookies:
        name = cookie.get("name")
        value = cookie.get("value")
        if name and value is not None:
            result[str(name)] = str(value)

    if "__session" not in result:
        raise ClerkLoginError("Login completed but '__session' cookie was not set")

    return result


def _wait_for_clerk(page: Page, timeout_ms: int) -> None:
    page.wait_for_function("window.Clerk && window.Clerk.loaded", timeout=timeout_ms)
    page.wait_for_timeout(2_000)


def _wait_for_session_cookie(page: Page, timeout_ms: int) -> None:
    page.wait_for_function(
        """() => document.cookie.includes('__session=')""",
        timeout=timeout_ms,
    )


def _evaluate_clerk(page: Page, script: str, payload: dict) -> dict:
    try:
        result = page.evaluate(script, payload)
    except PlaywrightError as exc:
        raise ClerkLoginError(f"Clerk browser sign-in failed: {exc}") from exc

    if not isinstance(result, dict):
        raise ClerkLoginError(f"Unexpected Clerk response: {result!r}")
    return result


def _raise_for_clerk_error(result: dict, *, email: str) -> None:
    if result.get("status") != "error":
        return

    code = str(result.get("code", "clerk_error"))
    message = str(result.get("message", "Clerk sign-in failed"))

    if code in {"captcha_invalid", "captcha_not_enabled"}:
        raise ClerkLoginError(
            "MacroPulse blocked automatic account creation (CAPTCHA) from the Coolify container. "
            "This happens on first sync when no MacroPulse account exists yet for the mail.tm address. "
            "Persist .pulse_inbox.json and .pulse_mailtm_state.json across runs, or set CLERK_SECRET_KEY "
            "only if you want backend account creation instead of browser sign-up."
        )

    if code == "form_identifier_not_found" and email:
        raise ClerkLoginError(
            f"No MacroPulse account exists for {email}. "
            "Automatic sign-up will be attempted on the next sync."
        )

    if code == "form_password_incorrect" and email:
        raise ClerkLoginError(
            f"Incorrect MacroPulse password for {email}. "
            "Use the password saved in .pulse_inbox.json from the first sync."
        )

    raise ClerkLoginError(f"Clerk sign-in failed ({code}): {message}")


def _clerk_sign_in_phase1(page: Page, *, email: str, password: str) -> dict:
    result = _evaluate_clerk(page, CLERK_SIGN_IN_JS, {"email": email, "password": password})
    if result.get("status") == "error" and result.get("code") == "form_identifier_not_found":
        return result
    _raise_for_clerk_error(result, email=email)
    return result


def _clerk_sign_up(page: Page, *, email: str, password: str) -> dict:
    result = _evaluate_clerk(page, CLERK_SIGN_UP_JS, {"email": email, "password": password})
    if result.get("status") == "error":
        _raise_for_clerk_error(result, email=email)
    return result


def _clerk_sign_up_verify(page: Page, *, email_code: str) -> dict:
    result = _evaluate_clerk(page, CLERK_SIGN_UP_VERIFY_JS, {"emailCode": email_code})
    _raise_for_clerk_error(result, email="")
    return result


def _complete_sign_up_result(
    page: Page,
    result: dict,
    *,
    email: str,
    email_code: str | None,
    mail_page: Page,
) -> dict:
    if result.get("status") == "needs_email_code":
        resolved_code = _resolve_email_code(email, email_code)
        if not resolved_code:
            resolved_code = _fetch_email_code_from_mail_tm(mail_page)
        return _clerk_sign_up_verify(page, email_code=resolved_code)

    if result.get("status") != "complete":
        raise ClerkLoginError(
            "MacroPulse sign-up did not complete "
            f"(status={result.get('status')}, missing={result.get('missingFields')})."
        )

    return result


def _attempt_auto_sign_up(
    page: Page,
    mail_page: Page,
    *,
    email: str,
    password: str,
    email_code: str | None,
) -> dict:
    page.wait_for_timeout(5_000)
    result = _clerk_sign_up(page, email=email, password=password)
    return _complete_sign_up_result(
        page,
        result,
        email=email,
        email_code=email_code,
        mail_page=mail_page,
    )


def _clerk_ticket_sign_in(page: Page, *, ticket: str) -> dict:
    result = _evaluate_clerk(page, CLERK_TICKET_SIGN_IN_JS, {"ticket": ticket})
    _raise_for_clerk_error(result, email="")
    return result


def _complete_auth_result(
    page: Page,
    result: dict,
    *,
    email: str,
    email_code: str | None,
    mail_page: Page,
) -> dict:
    if result.get("status") == "needs_email_code":
        resolved_code = _resolve_email_code(email, email_code)
        if not resolved_code and supports_auto_email_code(email):
            resolved_code = _fetch_email_code_from_mail_tm(mail_page)

        if not resolved_code:
            identifier = result.get("safeIdentifier") or email
            raise ClerkLoginError(
                "Clerk requires an email verification code after password sign-in. "
                f"Check {identifier} on https://mail.tm/en/ for the code, "
                "or set PULSE_MFA_CODE for a one-time override."
            )

        return _clerk_sign_in_phase2(page, email_code=resolved_code)

    if result.get("status") != "complete":
        raise ClerkLoginError(
            "Clerk sign-in did not complete "
            f"(status={result.get('status')}, factors={result.get('supportedSecondFactors')})."
        )

    return result


def _clerk_sign_in_phase2(page: Page, *, email_code: str) -> dict:
    result = _evaluate_clerk(page, CLERK_SIGN_IN_PHASE2_JS, {"emailCode": email_code})
    _raise_for_clerk_error(result, email="")
    return result


def _resolve_email_code(email: str, manual_code: str | None) -> str | None:
    if manual_code:
        return manual_code

    env_code = os.getenv("PULSE_MFA_CODE", "").strip()
    if env_code:
        return env_code

    return None


def _fetch_email_code_from_mail_tm(mail_page: Page) -> str:
    timeout = float(os.getenv("PULSE_EMAIL_CODE_TIMEOUT", "120"))
    poll_interval = float(os.getenv("PULSE_EMAIL_POLL_INTERVAL", "3"))

    try:
        return wait_for_verification_code(
            mail_page,
            timeout_seconds=timeout,
            poll_interval_seconds=poll_interval,
        )
    except TimeoutError as exc:
        raise ClerkLoginError(
            "Timed out waiting for Clerk verification code on https://mail.tm/en/. "
            "Ensure your MacroPulse account uses the disposable address shown there."
        ) from exc
    except RuntimeError as exc:
        raise ClerkLoginError(f"Failed to read mail.tm inbox: {exc}") from exc


def _launch_browser(playwright, *, headless: bool, storage_state: Path | None = None):
    browser = playwright.chromium.launch(
        headless=headless,
        args=["--disable-blink-features=AutomationControlled"],
    )
    context_options = {
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "viewport": {"width": 1280, "height": 720},
        "locale": "en-US",
    }
    if storage_state and storage_state.exists():
        context = browser.new_context(storage_state=str(storage_state), **context_options)
    else:
        context = browser.new_context(**context_options)
    return browser, context


def _resolve_mail_tm_credentials(
    mail_page: Page,
    *,
    requested_email: str,
    requested_password: str,
    saved: InboxCredentials | None,
) -> InboxCredentials:
    page_email = read_email_address(mail_page)

    if saved and saved.address.lower() != page_email.lower():
        raise ClerkLoginError(
            f"mail.tm inbox for {saved.address} was not restored (got {page_email}). "
            f"Ensure {storage_state_path()} persists across restarts, or delete "
            f"{inbox_credentials_path()} and run sync again to create a new inbox."
        )

    if requested_email.strip() and requested_email.strip().lower() != page_email.lower():
        raise ClerkLoginError(
            f"mail.tm inbox is {page_email}, but login was requested for {requested_email.strip()}."
        )

    password = requested_password.strip() or (saved.password if saved else generate_macro_pulse_password())
    credentials = InboxCredentials(address=page_email, password=password)

    if not saved or saved.address != credentials.address or saved.password != credentials.password:
        save_inbox_credentials(credentials)
        if not saved:
            print(
                "Created disposable inbox via mail.tm:\n"
                f"  email: {credentials.address}\n"
                f"  password: {credentials.password}\n"
                f"  saved to {inbox_credentials_path()}"
            )

    return credentials


def login_and_get_cookies(
    *,
    email: str,
    password: str,
    base_url: str = BASE_URL,
    headless: bool = True,
    timeout_ms: int = 90_000,
    email_code: str | None = None,
) -> dict[str, str]:
    sign_in_url = _sign_in_url(base_url=base_url)
    saved_inbox = load_saved_inbox_credentials()
    mailtm_state_path = storage_state_path()

    with sync_playwright() as playwright:
        browser, context = _launch_browser(
            playwright,
            headless=headless,
            storage_state=mailtm_state_path if saved_inbox else None,
        )
        mail_page = open_mail_tm(context, timeout_ms=timeout_ms)
        credentials = _resolve_mail_tm_credentials(
            mail_page,
            requested_email=email,
            requested_password=password,
            saved=saved_inbox,
        )
        resolved_email = credentials.address
        resolved_password = credentials.password

        page = context.new_page()

        try:
            page.goto(sign_in_url, wait_until="load", timeout=timeout_ms)
            _wait_for_clerk(page, timeout_ms)

            from scraper.clerk_backend import clerk_secret_key, ensure_user_sign_in_ticket

            ticket = None
            if clerk_secret_key():
                try:
                    ticket = ensure_user_sign_in_ticket(resolved_email, resolved_password)
                except RuntimeError as exc:
                    raise ClerkLoginError(str(exc)) from exc

            if ticket:
                result = _clerk_ticket_sign_in(page, ticket=ticket)
            else:
                result = _clerk_sign_in_phase1(
                    page,
                    email=resolved_email,
                    password=resolved_password,
                )

                if result.get("code") == "form_identifier_not_found":
                    result = _attempt_auto_sign_up(
                        page,
                        mail_page,
                        email=resolved_email,
                        password=resolved_password,
                        email_code=email_code,
                    )
                else:
                    result = _complete_auth_result(
                        page,
                        result,
                        email=resolved_email,
                        email_code=email_code,
                        mail_page=mail_page,
                    )

            if result.get("status") != "complete":
                raise ClerkLoginError(
                    "Clerk sign-in did not complete "
                    f"(status={result.get('status')}, factors={result.get('supportedSecondFactors')})."
                )

            _wait_for_session_cookie(page, timeout_ms)
            page.goto(f"{base_url.rstrip('/')}{PULSE_PATH}", wait_until="load", timeout=timeout_ms)
        except PlaywrightTimeoutError as exc:
            raise ClerkLoginError(
                "Timed out waiting for Clerk sign-in; verify credentials, MFA, or email code settings"
            ) from exc
        finally:
            save_storage_state(context, mailtm_state_path)
            cookies = context.cookies()
            browser.close()

    return _cookies_from_browser(cookies)
