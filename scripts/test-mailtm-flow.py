#!/usr/bin/env python3
"""Smoke test for mail.tm browser inbox + Clerk login integration."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _ok(message: str) -> None:
    print(f"PASS: {message}")


def _fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    sys.exit(1)


def test_mail_tm_provision_and_persist() -> str:
    from playwright.sync_api import sync_playwright

    from scraper.clerk_login import _launch_browser
    from scraper.mail_tm_browser import open_mail_tm, read_email_address, save_storage_state, storage_state_path

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        inbox_path = tmp_path / "inbox.json"
        state_path = tmp_path / "mailtm_state.json"

        import os

        os.environ["PULSE_INBOX_PATH"] = str(inbox_path)
        os.environ["PULSE_MAILTM_STATE_PATH"] = str(state_path)
        os.environ.pop("TURSO_DATABASE_URL", None)
        os.environ.pop("TURSO_AUTH_TOKEN", None)

        with sync_playwright() as playwright:
            browser, context = _launch_browser(playwright, headless=True)
            page = open_mail_tm(context)
            email = read_email_address(page)
            if "@" not in email:
                _fail(f"invalid email from mail.tm: {email!r}")
            save_storage_state(context, state_path)
            browser.close()

        if not state_path.exists():
            _fail("mail.tm browser state was not saved")

        with sync_playwright() as playwright:
            browser, context = _launch_browser(
                playwright,
                headless=True,
                storage_state=state_path,
            )
            page = open_mail_tm(context)
            restored_email = read_email_address(page)
            browser.close()

        if restored_email != email:
            _fail(f"inbox restore mismatch: {email} != {restored_email}")

        _ok(f"mail.tm provision + restore ({email})")
        return email


def test_login_flow() -> None:
    import os

    from scraper.clerk_login import login_and_get_cookies

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        inbox_path = tmp_path / "inbox.json"
        state_path = tmp_path / "mailtm_state.json"

        os.environ["PULSE_INBOX_PATH"] = str(inbox_path)
        os.environ["PULSE_MAILTM_STATE_PATH"] = str(state_path)
        os.environ.pop("TURSO_DATABASE_URL", None)
        os.environ.pop("TURSO_AUTH_TOKEN", None)

        try:
            cookies = login_and_get_cookies(
                email="",
                password="",
                headless=True,
                timeout_ms=120_000,
            )
        except Exception as exc:
            message = str(exc)
            if "No MacroPulse account exists" in message or "Register with email" in message:
                _ok(f"login reached expected registration gate ({message.splitlines()[0]})")
                if not inbox_path.exists():
                    _fail("inbox credentials were not saved before registration gate")
                saved = json.loads(inbox_path.read_text(encoding="utf-8"))
                if not saved.get("address") or not saved.get("password"):
                    _fail("saved inbox credentials are incomplete")
                _ok(f"saved inbox credentials ({saved['address']})")
                return
            raise

        if "__session" not in cookies:
            _fail("login succeeded but __session cookie missing")

        _ok(f"Clerk login succeeded for this run (__session length={len(cookies['__session'])})")


def main() -> int:
    sys.path.insert(0, str(ROOT))
    print("Testing mail.tm browser flow...")
    test_mail_tm_provision_and_persist()
    test_login_flow()
    print("All flow tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
