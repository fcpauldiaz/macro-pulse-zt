"""Stealth browser launch helpers to reduce bot detection in Playwright."""

from __future__ import annotations

import os
from pathlib import Path

from playwright.sync_api import Browser, BrowserContext, Playwright
from playwright_stealth import Stealth

STEALTH = Stealth(
    navigator_webdriver=True,
    navigator_languages=True,
    navigator_permissions=True,
    navigator_plugins=True,
    navigator_user_agent=True,
    webgl_vendor=True,
    media_codecs=True,
    chrome_app=True,
    chrome_csi=True,
    chrome_load_times=True,
    hairline=True,
    iframe_content_window=True,
)

CHROMIUM_ARGS = (
    "--disable-blink-features=AutomationControlled",
    "--disable-dev-shm-usage",
    "--no-sandbox",
    "--disable-infobars",
    "--window-size=1366,768",
)


def stealth_enabled() -> bool:
    explicit = os.environ.get("PULSE_DISABLE_STEALTH", "").strip().lower()
    return explicit not in {"1", "true", "yes"}


def apply_stealth(context: BrowserContext) -> None:
    if stealth_enabled():
        STEALTH.apply_stealth_sync(context)


def launch_stealth_browser(
    playwright: Playwright,
    *,
    headless: bool,
    storage_state: Path | None = None,
) -> tuple[Browser, BrowserContext]:
    browser = playwright.chromium.launch(
        headless=headless,
        args=list(CHROMIUM_ARGS),
    )
    context_options: dict = {
        "locale": "en-US",
        "timezone_id": "America/New_York",
        "viewport": {"width": 1366, "height": 768},
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "extra_http_headers": {"Accept-Language": "en-US,en;q=0.9"},
    }
    if storage_state and storage_state.exists():
        context_options["storage_state"] = str(storage_state)

    context = browser.new_context(**context_options)
    apply_stealth(context)
    return browser, context
