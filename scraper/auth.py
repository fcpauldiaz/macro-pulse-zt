from __future__ import annotations

from scraper.errors import ClerkLoginError, PulseDataError


def resolve_login_credentials() -> tuple[str, str]:
    from scraper.disposable_inbox import load_saved_inbox_credentials

    saved = load_saved_inbox_credentials()
    if saved:
        return saved.address, saved.password

    return "", ""


def ensure_cookies(*, base_url: str) -> dict[str, str]:
    email, password = resolve_login_credentials()

    from scraper.clerk_login import login_and_get_cookies

    try:
        return login_and_get_cookies(
            email=email,
            password=password,
            base_url=base_url,
        )
    except ClerkLoginError as exc:
        raise PulseDataError(str(exc)) from exc
