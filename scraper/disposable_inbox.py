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
MAIL_TD_API = "https://api.mail.td"
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
        "mail.td",
        "2200freefonts.com",
    }
)


@dataclass(frozen=True)
class InboxMessage:
    subject: str
    body: str
    sender: str


@dataclass(frozen=True)
class InboxCredentials:
    address: str
    password: str
    account_id: str = ""
    provider: str = "mail.td"


DEFAULT_INBOX_PATH = Path(".pulse_inbox.json")


def inbox_credentials_path() -> Path:
    return Path(os.environ.get("PULSE_INBOX_PATH", str(DEFAULT_INBOX_PATH)))


def smtp_key() -> str:
    return os.environ.get("SMTP_KEY", "").strip()


def _mail_td_headers(api_key: str) -> dict[str, str]:
    return {"X-API-KEY": api_key}


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
    account_id = str(raw.get("account_id", "")).strip()
    provider = str(raw.get("provider", "mail.td")).strip() or "mail.td"
    return InboxCredentials(
        address=address,
        password=password,
        account_id=account_id,
        provider=provider,
    )


PULSE_AUTH_INBOX_DDL = """
CREATE TABLE IF NOT EXISTS pulse_auth_inbox (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  address TEXT NOT NULL,
  password TEXT NOT NULL,
  account_id TEXT,
  provider TEXT NOT NULL DEFAULT 'mail.td',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""


def _ensure_inbox_schema(client) -> None:
    from scraper.turso_store import init_schema

    init_schema(client)
    client.execute(PULSE_AUTH_INBOX_DDL)
    try:
        client.execute("ALTER TABLE pulse_auth_inbox ADD COLUMN account_id TEXT")
    except Exception:
        pass
    try:
        client.execute(
            "ALTER TABLE pulse_auth_inbox ADD COLUMN provider TEXT NOT NULL DEFAULT 'mail.td'"
        )
    except Exception:
        pass


def _load_inbox_from_turso() -> InboxCredentials | None:
    if not os.environ.get("TURSO_DATABASE_URL", "").strip():
        return None

    try:
        from scraper.turso_store import create_client

        client = create_client()
        try:
            _ensure_inbox_schema(client)
            result = client.execute(
                "SELECT address, password, account_id, provider FROM pulse_auth_inbox WHERE id = 1"
            )
            rows = result.rows or []
            if not rows:
                return None
            row = rows[0]
            address = str(row[0]).strip()
            password = str(row[1]).strip()
            account_id = str(row[2] or "").strip()
            provider = str(row[3] or "mail.td").strip() or "mail.td"
            if not address or not password:
                return None
            return InboxCredentials(
                address=address,
                password=password,
                account_id=account_id,
                provider=provider,
            )
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
                "account_id": credentials.account_id,
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
                INSERT INTO pulse_auth_inbox (id, address, password, account_id, provider, created_at)
                VALUES (1, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(id) DO UPDATE SET
                  address = excluded.address,
                  password = excluded.password,
                  account_id = excluded.account_id,
                  provider = excluded.provider,
                  created_at = excluded.created_at
                """,
                [
                    credentials.address,
                    credentials.password,
                    credentials.account_id or None,
                    credentials.provider,
                ],
            )
        finally:
            client.close()
    except Exception:
        return


def _resolve_mail_td_account_id(credentials: InboxCredentials) -> InboxCredentials:
    if credentials.account_id:
        return credentials

    api_key = smtp_key()
    if not api_key:
        return credentials

    payload = _http_json(
        f"{MAIL_TD_API}/api/user/accounts",
        headers=_mail_td_headers(api_key),
    )
    if not isinstance(payload, dict):
        return credentials

    for item in payload.get("accounts", []) or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("address", "")).strip().lower() == credentials.address.lower():
            account_id = str(item.get("id", "")).strip()
            if account_id:
                return InboxCredentials(
                    address=credentials.address,
                    password=credentials.password,
                    account_id=account_id,
                    provider=credentials.provider,
                )

    return credentials


def ensure_inbox_credentials(*, prefix: str = "macro-pulse") -> InboxCredentials:
    """Resolve inbox credentials from saved file, Turso, or auto-provision mail.td."""
    api_key = smtp_key()
    if not api_key:
        raise ValueError(
            "Set SMTP_KEY in Coolify (mail.td Pro API token, td_...) for temp email access."
        )

    saved = load_saved_inbox_credentials()
    if saved:
        resolved = _resolve_mail_td_account_id(saved)
        save_inbox_credentials(resolved)
        return resolved

    credentials = provision_mail_td_inbox(prefix=prefix, api_key=api_key)
    save_inbox_credentials(credentials)
    print(
        "Created disposable inbox via mail.td:\n"
        f"  email: {credentials.address}\n"
        f"  password: {credentials.password}\n"
        f"  saved to {inbox_credentials_path()}\n"
        "Register this account at https://macro-wrap.vercel.app/sign-up, then re-run sync."
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
    if smtp_key():
        return True
    return is_disposable_email(email)


def create_inbox_for_email(email: str) -> DisposableInbox:
    local, domain = email.rsplit("@", 1)
    domain = domain.lower().strip()
    local = local.strip()

    api_key = smtp_key()
    if api_key and domain == "mail.td":
        saved = load_saved_inbox_credentials()
        if saved and saved.address.lower() == email.lower():
            resolved = _resolve_mail_td_account_id(saved)
            if resolved.account_id:
                return MailTdInbox(account_id=resolved.account_id, api_key=api_key)
        raise ValueError(
            f"No saved mail.td inbox for '{email}'. Run sync once to provision an inbox with SMTP_KEY."
        )

    if domain in {"mail.tm", "mail.gw"}:
        saved = load_saved_inbox_credentials()
        if saved and saved.address.lower() == email.lower() and saved.password:
            return MailTmInbox(address=saved.address, password=saved.password)

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
        "Set SMTP_KEY (temp email API key) and run sync to auto-provision an inbox."
    )


def provision_mail_td_inbox(*, prefix: str = "macro-pulse", api_key: str) -> InboxCredentials:
    address = f"{prefix}-{int(time.time())}@mail.td"
    password = secrets.token_urlsafe(18)

    payload = _http_json(
        f"{MAIL_TD_API}/api/accounts",
        method="POST",
        data={"address": address, "password": password},
        headers=_mail_td_headers(api_key),
    )
    if not isinstance(payload, dict) or not payload.get("id") or not payload.get("address"):
        raise RuntimeError("mail.td inbox creation failed")

    return InboxCredentials(
        address=str(payload["address"]),
        password=password,
        account_id=str(payload["id"]),
        provider="mail.td",
    )


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


class MailTdInbox(DisposableInbox):
    def __init__(self, account_id: str, api_key: str) -> None:
        self.account_id = account_id
        self.api_key = api_key

    def _auth_headers(self) -> dict[str, str]:
        return _mail_td_headers(self.api_key)

    def _fetch_messages(self) -> list[InboxMessage]:
        payload = _http_json(
            f"{MAIL_TD_API}/api/accounts/{quote(self.account_id)}/messages",
            headers=self._auth_headers(),
        )
        if not isinstance(payload, dict):
            return []

        messages: list[InboxMessage] = []
        for item in payload.get("messages", []) or []:
            if not isinstance(item, dict):
                continue
            message_id = item.get("id")
            if not message_id:
                continue
            detail = _http_json(
                f"{MAIL_TD_API}/api/accounts/{quote(self.account_id)}/messages/{quote(str(message_id))}",
                headers=self._auth_headers(),
            )
            if not isinstance(detail, dict):
                continue
            messages.append(
                InboxMessage(
                    subject=str(detail.get("subject", "")),
                    body=str(detail.get("text_body") or detail.get("html_body") or ""),
                    sender=str(detail.get("from") or detail.get("sender") or ""),
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
