"""Disposable inbox credential persistence and verification code parsing."""

from __future__ import annotations

import json
import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path

CODE_PATTERN = re.compile(r"\b(\d{6})\b")

DISPOSABLE_DOMAINS = frozenset(
    {
        "mail.tm",
        "mail.gw",
        "wshu.net",
        "2200freefonts.com",
    }
)


@dataclass(frozen=True)
class InboxCredentials:
    address: str
    password: str
    provider: str = "mail.tm"


DEFAULT_INBOX_PATH = Path(".pulse_inbox.json")


def inbox_credentials_path() -> Path:
    return Path(os.environ.get("PULSE_INBOX_PATH", str(DEFAULT_INBOX_PATH)))


def load_saved_inbox_credentials() -> InboxCredentials | None:
    path = inbox_credentials_path()
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            credentials = _credentials_from_dict(raw)
            if credentials:
                return credentials

    return _load_inbox_from_turso()


def _credentials_from_dict(raw: dict) -> InboxCredentials | None:
    address = str(raw.get("address", "")).strip()
    password = str(raw.get("password", "")).strip()
    if not address or not password:
        return None
    provider = str(raw.get("provider", "mail.tm")).strip() or "mail.tm"
    return InboxCredentials(address=address, password=password, provider=provider)


PULSE_AUTH_INBOX_DDL = """
CREATE TABLE IF NOT EXISTS pulse_auth_inbox (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  address TEXT NOT NULL,
  password TEXT NOT NULL,
  account_id TEXT,
  provider TEXT NOT NULL DEFAULT 'mail.tm',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""


def _ensure_inbox_schema(client) -> None:
    from scraper.turso_store import init_schema

    init_schema(client)
    client.execute(PULSE_AUTH_INBOX_DDL)


def _load_inbox_from_turso() -> InboxCredentials | None:
    if not os.environ.get("TURSO_DATABASE_URL", "").strip():
        return None

    try:
        from scraper.turso_store import create_client

        client = create_client()
        try:
            _ensure_inbox_schema(client)
            result = client.execute(
                "SELECT address, password, provider FROM pulse_auth_inbox WHERE id = 1"
            )
            rows = result.rows or []
            if not rows:
                return None
            row = rows[0]
            address = str(row[0]).strip()
            password = str(row[1]).strip()
            provider = str(row[2] or "mail.tm").strip() or "mail.tm"
            if not address or not password:
                return None
            return InboxCredentials(address=address, password=password, provider=provider)
        finally:
            client.close()
    except Exception:
        return None


def save_inbox_credentials(credentials: InboxCredentials) -> None:
    path = inbox_credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "address": credentials.address,
                "password": credentials.password,
                "provider": credentials.provider,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    _save_inbox_to_turso(credentials)


def _save_inbox_to_turso(credentials: InboxCredentials) -> None:
    if not os.environ.get("TURSO_DATABASE_URL", "").strip():
        return

    try:
        from scraper.turso_store import create_client

        client = create_client()
        try:
            _ensure_inbox_schema(client)
            client.execute(
                """
                INSERT INTO pulse_auth_inbox (id, address, password, provider, created_at)
                VALUES (1, ?, ?, ?, datetime('now'))
                ON CONFLICT(id) DO UPDATE SET
                  address = excluded.address,
                  password = excluded.password,
                  provider = excluded.provider,
                  created_at = excluded.created_at
                """,
                [credentials.address, credentials.password, credentials.provider],
            )
        finally:
            client.close()
    except Exception:
        return


def generate_macro_pulse_password() -> str:
    return secrets.token_urlsafe(18)


def ensure_inbox_credentials() -> InboxCredentials:
    """Return persisted inbox credentials. Provisioning happens during browser login."""
    saved = load_saved_inbox_credentials()
    if saved:
        return saved

    raise ValueError(
        "No saved mail.tm inbox yet. Run sync once to open https://mail.tm/en/, "
        "create a disposable email, and persist credentials automatically."
    )


def is_disposable_email(email: str) -> bool:
    domain = email.rsplit("@", 1)[-1].lower().strip()
    return domain in DISPOSABLE_DOMAINS or domain.endswith(".net")


def supports_auto_email_code(email: str) -> bool:
    explicit = os.environ.get("PULSE_USE_DISPOSABLE_INBOX", "").strip().lower()
    if explicit in {"0", "false", "no"}:
        return False
    if explicit in {"1", "true", "yes"}:
        return True
    return is_disposable_email(email)


def extract_verification_code(text: str, pattern: re.Pattern[str] = CODE_PATTERN) -> str | None:
    match = pattern.search(text)
    return match.group(1) if match else None
