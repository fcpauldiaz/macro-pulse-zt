from __future__ import annotations

import os
from pathlib import Path

from scraper.errors import ClerkLoginError, PulseDataError


def session_path() -> Path:
    return Path(os.getenv("PULSE_SESSION_PATH", ".pulse_session.json"))


def resolve_cookies() -> dict[str, str]:
    clerk_session = os.getenv("CLERK_SESSION", "").strip()
    if clerk_session:
        return {"__session": clerk_session}

    path = session_path()
    if not path.exists():
        return {}

    from scraper.clerk_login import load_session_cookies

    return load_session_cookies(path)


def resolve_login_credentials() -> tuple[str, str]:
    from scraper.disposable_inbox import ensure_inbox_credentials

    inbox = ensure_inbox_credentials()
    if not inbox.password:
        raise PulseDataError(
            "Pulse API requires authentication. Set CLERK_SESSION, PULSE_SESSION_PATH, "
            "or PULSE_EMAIL_PASSWORD (used for both mail.tm inbox and MacroPulse login)."
        )

    return inbox.address, inbox.password


def ensure_cookies(*, base_url: str) -> dict[str, str]:
    cookies = resolve_cookies()
    if cookies:
        return cookies

    email, password = resolve_login_credentials()

    from scraper.clerk_login import login_and_save_session

    try:
        return login_and_save_session(
            email=email,
            password=password,
            session_path=session_path(),
            base_url=base_url,
        )
    except ClerkLoginError as exc:
        raise PulseDataError(str(exc)) from exc
