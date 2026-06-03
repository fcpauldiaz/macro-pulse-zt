"""Browser automation helpers for mail.tm disposable inbox."""

from __future__ import annotations

import os
import time
from pathlib import Path

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

from scraper.disposable_inbox import CODE_PATTERN, extract_verification_code

MAIL_TM_URL = "https://mail.tm/en/"
EMAIL_INPUT = 'input[class*="select-all"]'
MAIN_PANEL = ".relative.z-0.flex-1"
MARKETING_SNIPPET = "Protect your personal email address from spam"
DEFAULT_STORAGE_PATH = Path(".pulse_mailtm_state.json")


def storage_state_path() -> Path:
    return Path(os.environ.get("PULSE_MAILTM_STATE_PATH", str(DEFAULT_STORAGE_PATH)))


def open_mail_tm(context, *, timeout_ms: int = 90_000) -> Page:
    page = context.new_page()
    page.goto(MAIL_TM_URL, wait_until="domcontentloaded", timeout=timeout_ms)
    page.wait_for_timeout(3_000)
    return page


def read_email_address(page: Page) -> str:
    email_input = page.locator(EMAIL_INPUT).first
    email_input.wait_for(state="visible", timeout=30_000)

    for _ in range(15):
        email = email_input.input_value().strip()
        if "@" in email:
            return email
        page.wait_for_timeout(1_000)

    raise RuntimeError("Could not read disposable email from mail.tm")


def save_storage_state(context, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    context.storage_state(path=str(path))


def _main_text(page: Page) -> str:
    return page.locator(MAIN_PANEL).inner_text(timeout=10_000)


def inbox_has_messages(page: Page) -> bool:
    return MARKETING_SNIPPET not in _main_text(page)


def refresh_inbox(page: Page, *, timeout_ms: int = 90_000) -> None:
    page.goto(MAIL_TM_URL, wait_until="domcontentloaded", timeout=timeout_ms)
    page.wait_for_timeout(2_000)


def _extract_code_from_open_message(page: Page) -> str | None:
    main_text = _main_text(page)
    return extract_verification_code(main_text, CODE_PATTERN)


def _open_message_candidates(page: Page) -> str | None:
    candidates = page.locator(
        f"{MAIN_PANEL} a, {MAIN_PANEL} li, {MAIN_PANEL} tr, {MAIN_PANEL} [role='button']"
    )
    skip_labels = {"Temp Mail", "Refresh", "Inbox"}
    skip_hrefs = ("/faq", "/privacy", "/feedback", "/contact")

    for index in range(candidates.count()):
        element = candidates.nth(index)
        label = element.inner_text().strip()
        href = element.get_attribute("href") or ""
        if not label or label in skip_labels:
            continue
        if any(part in href for part in skip_hrefs):
            continue

        try:
            element.click(timeout=3_000)
            page.wait_for_timeout(1_500)
            code = _extract_code_from_open_message(page)
            if code:
                return code
            refresh_inbox(page)
        except PlaywrightTimeoutError:
            continue

    return None


def wait_for_verification_code(
    page: Page,
    *,
    timeout_seconds: float = 120.0,
    poll_interval_seconds: float = 3.0,
) -> str:
    deadline = time.monotonic() + timeout_seconds

    while time.monotonic() < deadline:
        refresh_inbox(page)

        if inbox_has_messages(page):
            code = _extract_code_from_open_message(page)
            if code:
                return code

            code = _open_message_candidates(page)
            if code:
                return code

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        page.wait_for_timeout(min(poll_interval_seconds, remaining) * 1_000)

    raise TimeoutError(f"No verification code received on mail.tm within {timeout_seconds:.0f}s")
