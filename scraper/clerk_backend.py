"""Clerk Backend API helpers for automated account provisioning and ticket sign-in."""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

CLERK_BACKEND_API = "https://api.clerk.com/v1"


def clerk_secret_key() -> str:
    return os.environ.get("CLERK_SECRET_KEY", "").strip()


def _backend_json(
    path: str,
    *,
    secret_key: str,
    method: str = "GET",
    data: dict[str, Any] | None = None,
) -> object:
    url = f"{CLERK_BACKEND_API}{path}"
    headers = {
        "Authorization": f"Bearer {secret_key}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "macro-pulse-scraper/1.0",
    }
    body = json.dumps(data).encode("utf-8") if data is not None else None
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else None
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Clerk Backend API {method} {path} failed ({exc.code}): {detail}") from exc


def get_user_id_by_email(email: str, *, secret_key: str) -> str | None:
    encoded = quote(email, safe="")
    payload = _backend_json(
        f"/users?email_address[]={encoded}&limit=1",
        secret_key=secret_key,
    )
    if not isinstance(payload, list) or not payload:
        return None

    first = payload[0]
    if not isinstance(first, dict) or not first.get("id"):
        return None

    return str(first["id"])


def create_user(email: str, password: str, *, secret_key: str) -> str:
    payload = _backend_json(
        "/users",
        secret_key=secret_key,
        method="POST",
        data={
            "email_address": [email],
            "password": password,
            "first_name": "Macro",
            "last_name": "Pulse",
        },
    )
    if not isinstance(payload, dict) or not payload.get("id"):
        raise RuntimeError(f"Clerk user creation returned unexpected payload: {payload!r}")
    return str(payload["id"])


def ensure_user_id(email: str, password: str, *, secret_key: str) -> str:
    existing = get_user_id_by_email(email, secret_key=secret_key)
    if existing:
        return existing

    try:
        return create_user(email, password, secret_key=secret_key)
    except RuntimeError as exc:
        if "form_identifier_exists" in str(exc) or "already exists" in str(exc).lower():
            existing = get_user_id_by_email(email, secret_key=secret_key)
            if existing:
                return existing
        raise


def create_sign_in_token(user_id: str, *, secret_key: str) -> str:
    payload = _backend_json(
        "/sign_in_tokens",
        secret_key=secret_key,
        method="POST",
        data={"user_id": user_id, "expires_in_seconds": 86_400},
    )
    if not isinstance(payload, dict):
        raise RuntimeError(f"Unexpected sign-in token response: {payload!r}")

    token = payload.get("token") or payload.get("jwt")
    if not token:
        raise RuntimeError(f"Sign-in token missing from response: {payload!r}")
    return str(token)


def fetch_testing_token(*, secret_key: str) -> str:
    payload = _backend_json("/testing_tokens", secret_key=secret_key, method="POST", data={})
    if not isinstance(payload, dict) or not payload.get("token"):
        raise RuntimeError(f"Testing token missing from response: {payload!r}")
    return str(payload["token"])


def resolve_testing_token() -> str | None:
    explicit = os.environ.get("CLERK_TESTING_TOKEN", "").strip()
    if explicit:
        return explicit

    secret_key = clerk_secret_key()
    if not secret_key:
        return None

    try:
        return fetch_testing_token(secret_key=secret_key)
    except RuntimeError:
        return None


def ensure_user_sign_in_ticket(email: str, password: str) -> str | None:
    secret_key = clerk_secret_key()
    if not secret_key:
        return None

    user_id = ensure_user_id(email, password, secret_key=secret_key)
    return create_sign_in_token(user_id, secret_key=secret_key)
