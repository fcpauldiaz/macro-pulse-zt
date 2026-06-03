from __future__ import annotations

import os
import secrets
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
    from scraper.disposable_inbox import load_saved_inbox_credentials

    saved = load_saved_inbox_credentials()
    if saved:
        return saved.address, saved.password

    return "", ""


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
