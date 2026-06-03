from __future__ import annotations

import os
from pathlib import Path

from scraper.errors import PulseDataError


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


def require_session_cookies(*, base_url: str) -> dict[str, str]:
    cookies = resolve_cookies()
    if cookies:
        return cookies

    raise PulseDataError(
        "Pulse API requires a Clerk session cookie. The public endpoint is now protected.\n"
        "Set CLERK_SESSION to your __session cookie value from macro-wrap.vercel.app "
        "(DevTools → Application → Cookies), or mount PULSE_SESSION_PATH with a saved "
        "session JSON file.\n"
        "Playwright login is not used in the scheduled sync task."
    )
