"""Poll disposable email inboxes for Clerk MFA verification codes."""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

CODE_PATTERN = re.compile(r"\b(\d{6})\b")
MAIL_TM_API = "https://api.mail.tm"
TEMPMAIL_API = "https://api.tempmail.lol/v2"

DISPOSABLE_DOMAINS = frozenset(
    {
        "1secmail.com",
        "1secmail.org",
        "1secmail.net",
        "esiix.com",
        "wwjmp.com",
        "guerrillamailblock.com",
        "grr.la",
        "guerrillamail.com",
        "guerrillamail.net",
        "guerrillamail.org",
        "sharklasers.com",
        "mail.tm",
        "mail.gw",
        "2200freefonts.com",
    }
)


@dataclass(frozen=True)
class InboxMessage:
    subject: str
    body: str
    sender: str


@dataclass(frozen=True)
class MailTmCredentials:
    address: str
    password: str


DEFAULT_INBOX_PATH = Path(".pulse_inbox.json")


def inbox_credentials_path() -> Path:
    return Path(os.environ.get("PULSE_INBOX_PATH", str(DEFAULT_INBOX_PATH)))


def _apply_inbox_env(credentials: MailTmCredentials) -> None:
    os.environ["PULSE_EMAIL"] = credentials.address
    os.environ["PULSE_EMAIL_PASSWORD"] = credentials.password


def load_saved_inbox_credentials() -> MailTmCredentials | None:
    path = inbox_credentials_path()
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            address = str(raw.get("address", "")).strip()
            password = str(raw.get("password", "")).strip()
            if address and password:
                return MailTmCredentials(address=address, password=password)

    return _load_inbox_from_turso()


PULSE_AUTH_INBOX_DDL = """
CREATE TABLE IF NOT EXISTS pulse_auth_inbox (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  address TEXT NOT NULL,
  password TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""


def _load_inbox_from_turso() -> MailTmCredentials | None:
    if not os.environ.get("TURSO_DATABASE_URL", "").strip():
        return None

    try:
        from scraper.turso_store import create_client, init_schema

        client = create_client()
        try:
            init_schema(client)
            client.execute(PULSE_AUTH_INBOX_DDL)
            result = client.execute("SELECT address, password FROM pulse_auth_inbox WHERE id = 1")
            rows = result.rows or []
            if not rows:
                return None
            row = rows[0]
            address = str(row[0]).strip()
            password = str(row[1]).strip()
            if not address or not password:
                return None
            return MailTmCredentials(address=address, password=password)
        finally:
            client.close()
    except Exception:
        return None


def save_inbox_credentials(credentials: MailTmCredentials) -> None:
    path = inbox_credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"address": credentials.address, "password": credentials.password},
            indent=2,
        ),
        encoding="utf-8",
    )
    _save_inbox_to_turso(credentials)


def _save_inbox_to_turso(credentials: MailTmCredentials) -> None:
    if not os.environ.get("TURSO_DATABASE_URL", "").strip():
        return

    try:
        from scraper.turso_store import create_client, init_schema

        client = create_client()
        try:
            init_schema(client)
            client.execute(PULSE_AUTH_INBOX_DDL)
            client.execute(
                """
                INSERT INTO pulse_auth_inbox (id, address, password, created_at)
                VALUES (1, ?, ?, datetime('now'))
                ON CONFLICT(id) DO UPDATE SET
                  address = excluded.address,
                  password = excluded.password,
                  created_at = excluded.created_at
                """,
                [credentials.address, credentials.password],
            )
        finally:
            client.close()
    except Exception:
        return


def _resolve_account_password() -> str | None:
    return (
        os.environ.get("PULSE_EMAIL_PASSWORD", "").strip()
        or os.environ.get("PULSE_PASSWORD", "").strip()
        or None
    )


def ensure_inbox_credentials(*, prefix: str = "macro-pulse") -> MailTmCredentials:
    """Resolve inbox credentials from env, saved file, Turso, or auto-provision mail.tm."""
    email = os.environ.get("PULSE_EMAIL", "").strip()
    inbox_password = os.environ.get("PULSE_EMAIL_PASSWORD", "").strip()
    account_password = _resolve_account_password()

    if email and inbox_password:
        return MailTmCredentials(address=email, password=inbox_password)

    if email and account_password:
        credentials = MailTmCredentials(address=email, password=account_password)
        save_inbox_credentials(credentials)
        _apply_inbox_env(credentials)
        return credentials

    saved = load_saved_inbox_credentials()
    if saved:
        _apply_inbox_env(saved)
        save_inbox_credentials(saved)
        return saved

    if not account_password:
        raise ValueError(
            "Set PULSE_EMAIL_PASSWORD in Coolify (same password for mail.tm inbox and MacroPulse login)."
        )

    credentials = provision_mail_tm_inbox(prefix=prefix, password=account_password)
    save_inbox_credentials(credentials)
    _apply_inbox_env(credentials)
    print(
        "Created disposable inbox:\n"
        f"  PULSE_EMAIL={credentials.address}\n"
        f"  PULSE_EMAIL_PASSWORD={credentials.password}\n"
        f"  saved to {inbox_credentials_path()}\n"
        "Add both values to Coolify env so they persist across container restarts."
    )
    return credentials


@dataclass(frozen=True)
class TempMailCredentials:
    address: str
    token: str


class DisposableInbox(ABC):
    @abstractmethod
    async def wait_for_verification_code(
        self,
        *,
        timeout_seconds: float = 120.0,
        poll_interval_seconds: float = 3.0,
        code_pattern: re.Pattern[str] = CODE_PATTERN,
    ) -> str:
        raise NotImplementedError

    def wait_for_verification_code_sync(
        self,
        *,
        timeout_seconds: float = 120.0,
        poll_interval_seconds: float = 3.0,
        code_pattern: re.Pattern[str] = CODE_PATTERN,
    ) -> str:
        return asyncio.run(
            self.wait_for_verification_code(
                timeout_seconds=timeout_seconds,
                poll_interval_seconds=poll_interval_seconds,
                code_pattern=code_pattern,
            )
        )


def is_disposable_email(email: str) -> bool:
    domain = email.rsplit("@", 1)[-1].lower().strip()
    return domain in DISPOSABLE_DOMAINS


def supports_auto_email_code(email: str) -> bool:
    explicit = os.environ.get("PULSE_USE_DISPOSABLE_INBOX", "").strip().lower()
    if explicit in {"0", "false", "no"}:
        return False
    if explicit in {"1", "true", "yes"}:
        return True
    if os.environ.get("PULSE_EMAIL_TOKEN", "").strip():
        return True
    if os.environ.get("PULSE_EMAIL_PASSWORD", "").strip():
        return True
    return is_disposable_email(email)


def create_inbox_for_email(email: str) -> DisposableInbox:
    local, domain = email.rsplit("@", 1)
    domain = domain.lower().strip()
    local = local.strip()

    token = os.environ.get("PULSE_EMAIL_TOKEN", "").strip()
    if token:
        return TempMailLolInbox(address=email, token=token)

    password = os.environ.get("PULSE_EMAIL_PASSWORD", "").strip()
    if password:
        return MailTmInbox(address=email, password=password)

    if domain in {"1secmail.com", "1secmail.org", "1secmail.net", "esiix.com", "wwjmp.com"}:
        return OneSecMailInbox(login=local, domain=domain)

    if domain in {
        "guerrillamailblock.com",
        "grr.la",
        "guerrillamail.com",
        "guerrillamail.net",
        "guerrillamail.org",
        "sharklasers.com",
    }:
        return GuerrillaMailInbox(email=email)

    raise ValueError(
        f"No inbox API configured for '{email}'. "
        "Set PULSE_EMAIL_PASSWORD, run sync/login to auto-create a mail.tm inbox, "
        "or set PULSE_EMAIL_TOKEN for tempmail.lol."
    )


def provision_mail_tm_inbox(*, prefix: str = "macro-pulse", password: str | None = None) -> MailTmCredentials:
    domains_payload = _http_json(f"{MAIL_TM_API}/domains")
    if not isinstance(domains_payload, list) or not domains_payload:
        raise RuntimeError("mail.tm returned no active domains")

    first_domain = domains_payload[0]
    if not isinstance(first_domain, dict) or not first_domain.get("domain"):
        raise RuntimeError("mail.tm domain response was invalid")

    domain = str(first_domain["domain"])
    address = f"{prefix}-{int(time.time())}@{domain}"
    resolved_password = password.strip() if password and password.strip() else secrets.token_urlsafe(18)

    _http_json(
        f"{MAIL_TM_API}/accounts",
        method="POST",
        data={"address": address, "password": resolved_password},
    )
    return MailTmCredentials(address=address, password=resolved_password)


def provision_tempmail_inbox(*, prefix: str = "macro-pulse") -> TempMailCredentials:
    payload = _http_json(
        f"{TEMPMAIL_API}/inbox/create",
        method="POST",
        data={"prefix": prefix},
    )
    if not isinstance(payload, dict) or not payload.get("address") or not payload.get("token"):
        raise RuntimeError("tempmail.lol inbox creation failed")
    return TempMailCredentials(address=str(payload["address"]), token=str(payload["token"]))


def extract_verification_code(text: str, pattern: re.Pattern[str] = CODE_PATTERN) -> str | None:
    match = pattern.search(text)
    return match.group(1) if match else None


def _http_json(url: str, *, method: str = "GET", data: dict | None = None, headers: dict | None = None) -> object:
    body = None
    req_headers = {"Accept": "application/json", "User-Agent": "macro-pulse-scraper/1.0"}
    if headers:
        req_headers.update(headers)
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        req_headers["Content-Type"] = "application/json"
    request = Request(url, data=body, headers=req_headers, method=method)
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else None
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} for {url}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"Network error for {url}: {exc}") from exc


async def _poll_inbox(
    fetch_messages: Callable[[], list[InboxMessage]],
    *,
    timeout_seconds: float,
    poll_interval_seconds: float,
    code_pattern: re.Pattern[str],
) -> str:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        messages = await asyncio.to_thread(fetch_messages)
        for message in messages:
            for text in (message.subject, message.body):
                code = extract_verification_code(text, code_pattern)
                if code:
                    return code
        await asyncio.sleep(poll_interval_seconds)
    raise TimeoutError(f"No verification code received within {timeout_seconds:.0f}s")


class OneSecMailInbox(DisposableInbox):
    BASE = "https://www.1secmail.com/api/v1/"

    def __init__(self, login: str, domain: str) -> None:
        self.login = login
        self.domain = domain

    def _fetch_messages(self) -> list[InboxMessage]:
        url = (
            f"{self.BASE}?action=getMessages"
            f"&login={quote(self.login)}&domain={quote(self.domain)}"
        )
        payload = _http_json(url)
        if not isinstance(payload, list):
            return []

        messages: list[InboxMessage] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            message_id = item.get("id")
            if message_id is None:
                continue
            read_url = (
                f"{self.BASE}?action=readMessage"
                f"&login={quote(self.login)}&domain={quote(self.domain)}"
                f"&id={quote(str(message_id))}"
            )
            detail = _http_json(read_url)
            if not isinstance(detail, dict):
                continue
            messages.append(
                InboxMessage(
                    subject=str(detail.get("subject", "")),
                    body=str(detail.get("textBody") or detail.get("htmlBody") or ""),
                    sender=str(detail.get("from", "")),
                )
            )
        return messages

    async def wait_for_verification_code(
        self,
        *,
        timeout_seconds: float = 120.0,
        poll_interval_seconds: float = 3.0,
        code_pattern: re.Pattern[str] = CODE_PATTERN,
    ) -> str:
        return await _poll_inbox(
            self._fetch_messages,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
            code_pattern=code_pattern,
        )


class MailTmInbox(DisposableInbox):
    def __init__(self, address: str, password: str) -> None:
        self.address = address
        self.password = password
        self.token = self._authenticate()

    def _authenticate(self) -> str:
        payload = _http_json(
            f"{MAIL_TM_API}/token",
            method="POST",
            data={"address": self.address, "password": self.password},
        )
        if not isinstance(payload, dict) or not payload.get("token"):
            raise RuntimeError(f"mail.tm authentication failed for {self.address}")
        return str(payload["token"])

    def _fetch_messages(self) -> list[InboxMessage]:
        payload = _http_json(
            f"{MAIL_TM_API}/messages",
            headers={"Authorization": f"Bearer {self.token}"},
        )
        if not isinstance(payload, list):
            return []

        messages: list[InboxMessage] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            message_id = item.get("id")
            if not message_id:
                continue
            detail = _http_json(
                f"{MAIL_TM_API}/messages/{message_id}",
                headers={"Authorization": f"Bearer {self.token}"},
            )
            if not isinstance(detail, dict):
                continue
            messages.append(
                InboxMessage(
                    subject=str(detail.get("subject", "")),
                    body=str(detail.get("text") or detail.get("html") or ""),
                    sender=str((detail.get("from") or {}).get("address", "")),
                )
            )
        return messages

    async def wait_for_verification_code(
        self,
        *,
        timeout_seconds: float = 120.0,
        poll_interval_seconds: float = 3.0,
        code_pattern: re.Pattern[str] = CODE_PATTERN,
    ) -> str:
        return await _poll_inbox(
            self._fetch_messages,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
            code_pattern=code_pattern,
        )


class TempMailLolInbox(DisposableInbox):
    def __init__(self, address: str, token: str) -> None:
        self.address = address
        self.token = token

    def _fetch_messages(self) -> list[InboxMessage]:
        payload = _http_json(f"{TEMPMAIL_API}/inbox?token={quote(self.token)}")
        if not isinstance(payload, dict):
            return []

        messages: list[InboxMessage] = []
        for item in payload.get("emails", []) or []:
            if not isinstance(item, dict):
                continue
            messages.append(
                InboxMessage(
                    subject=str(item.get("subject", "")),
                    body=str(item.get("body") or item.get("html") or ""),
                    sender=str(item.get("from", "")),
                )
            )
        return messages

    async def wait_for_verification_code(
        self,
        *,
        timeout_seconds: float = 120.0,
        poll_interval_seconds: float = 3.0,
        code_pattern: re.Pattern[str] = CODE_PATTERN,
    ) -> str:
        return await _poll_inbox(
            self._fetch_messages,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
            code_pattern=code_pattern,
        )


class GuerrillaMailInbox(DisposableInbox):
    API = "https://api.guerrillamail.com/ajax.php"

    def __init__(self, email: str) -> None:
        self.email = email
        self.session_id: str | None = None
        self._ensure_session()

    def _ensure_session(self) -> None:
        local = self.email.split("@")[0]
        payload = _http_json(f"{self.API}?f=get_email_address&email_user={quote(local)}")
        if isinstance(payload, dict):
            self.session_id = str(payload.get("sid_token", "")) or None

    def _fetch_messages(self) -> list[InboxMessage]:
        if not self.session_id:
            self._ensure_session()
        if not self.session_id:
            return []

        payload = _http_json(f"{self.API}?f=check_email&seq=0&sid_token={quote(self.session_id)}")
        if not isinstance(payload, dict):
            return []

        messages: list[InboxMessage] = []
        for item in payload.get("list", []) or []:
            if not isinstance(item, dict):
                continue
            mail_id = item.get("mail_id")
            if not mail_id:
                continue
            detail = _http_json(
                f"{self.API}?f=fetch_email&email_id={quote(str(mail_id))}"
                f"&sid_token={quote(self.session_id)}"
            )
            if not isinstance(detail, dict):
                continue
            messages.append(
                InboxMessage(
                    subject=str(detail.get("mail_subject", "")),
                    body=str(detail.get("mail_body", "")),
                    sender=str(detail.get("mail_from", "")),
                )
            )
        return messages

    async def wait_for_verification_code(
        self,
        *,
        timeout_seconds: float = 120.0,
        poll_interval_seconds: float = 3.0,
        code_pattern: re.Pattern[str] = CODE_PATTERN,
    ) -> str:
        return await _poll_inbox(
            self._fetch_messages,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
            code_pattern=code_pattern,
        )
