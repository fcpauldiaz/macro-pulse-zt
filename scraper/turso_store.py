from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from scraper.auth import require_session_cookies
from scraper.errors import TursoStoreError
from scraper.pulse_client import (
    BASE_URL,
    PulseData,
    extract_momentum_rows,
    extract_quant_score_rows,
    fetch_pulse_data,
    get_regime,
)

import libsql_client

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "db" / "schema.sql"


@dataclass(frozen=True)
class SyncResult:
    snapshot_date: str
    regime: str
    momentum_count: int
    quant_count: int
    opened_positions: int
    updated_positions: int
    closed_positions: int


def _today() -> str:
    return date.today().isoformat()


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def resolve_database_url() -> tuple[str, str | None]:
    url = os.getenv("TURSO_DATABASE_URL", "file:local.db")
    token = os.getenv("TURSO_AUTH_TOKEN")
    return url, token


def create_client() -> libsql_client.Client:
    url, token = resolve_database_url()
    if url.startswith("file:"):
        return libsql_client.create_client_sync(url)
    if not token:
        raise TursoStoreError("TURSO_AUTH_TOKEN is required for remote Turso databases")
    return libsql_client.create_client_sync(url, auth_token=token)


def init_schema(client: libsql_client.Client) -> None:
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    statements = [part.strip() for part in schema.split(";") if part.strip()]
    for statement in statements:
        client.execute(statement)


def _price_lookup(data: PulseData) -> dict[str, float]:
    prices: dict[str, float] = {}

    for row in extract_momentum_rows(data, ready_to_buy=False):
        symbol = row.get("symbol")
        price = row.get("price")
        if isinstance(symbol, str) and isinstance(price, (int, float)):
            prices[symbol.upper()] = float(price)

    for row in data.get("quantScoreSignals") or []:
        if not isinstance(row, dict):
            continue
        symbol = row.get("symbol")
        price = row.get("price")
        if isinstance(symbol, str) and isinstance(price, (int, float)):
            prices[symbol.upper()] = float(price)

    return prices


def _fetch_snapshot_id(client: libsql_client.Client, snapshot_date: str) -> int | None:
    result = client.execute(
        "SELECT id FROM daily_snapshots WHERE snapshot_date = ?",
        [snapshot_date],
    )
    if not result.rows:
        return None
    return int(result.rows[0][0])


def _insert_snapshot(
    client: libsql_client.Client,
    *,
    snapshot_date: str,
    regime: str,
    pulse_updated_at: str | None,
) -> int:
    existing = _fetch_snapshot_id(client, snapshot_date)
    if existing is not None:
        return existing

    client.execute(
        """
        INSERT INTO daily_snapshots (snapshot_date, regime, pulse_updated_at)
        VALUES (?, ?, ?)
        """,
        [snapshot_date, regime, pulse_updated_at],
    )
    created = _fetch_snapshot_id(client, snapshot_date)
    if created is None:
        raise TursoStoreError(f"Failed to create snapshot for {snapshot_date}")
    return created


def _insert_daily_signals(
    client: libsql_client.Client,
    *,
    snapshot_id: int,
    signal_type: str,
    rows: list[dict[str, Any]],
) -> int:
    inserted = 0
    for row in rows:
        symbol = row.get("symbol")
        if not isinstance(symbol, str) or not symbol:
            continue

        client.execute(
            """
            INSERT OR IGNORE INTO daily_signals (
              snapshot_id, signal_type, symbol, etf, price, sma50, beta,
              quant_score, rsi, signal_label, sector, industry
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                snapshot_id,
                signal_type,
                symbol.upper(),
                row.get("etf"),
                row.get("price"),
                row.get("sma50"),
                row.get("beta"),
                row.get("quantScore") if signal_type == "quant" else row.get("quant_score"),
                row.get("rsi"),
                row.get("signal"),
                row.get("sector"),
                row.get("industry"),
            ],
        )
        inserted += 1
    return inserted


def _open_positions(
    client: libsql_client.Client,
    signal_type: str,
) -> dict[str, tuple[int, float, str]]:
    result = client.execute(
        """
        SELECT id, symbol, entry_price, entry_date
        FROM trade_positions
        WHERE status = 'open' AND signal_type = ?
        """,
        [signal_type],
    )
    return {
        str(row[1]).upper(): (int(row[0]), float(row[2]), str(row[3]))
        for row in result.rows
    }


def _update_trade_positions(
    client: libsql_client.Client,
    *,
    signal_type: str,
    snapshot_date: str,
    active_rows: list[dict[str, Any]],
    price_lookup: dict[str, float],
) -> tuple[int, int, int]:
    opened = 0
    updated = 0
    closed = 0

    active_symbols = {
        str(row.get("symbol", "")).upper()
        for row in active_rows
        if isinstance(row.get("symbol"), str) and row.get("symbol")
    }
    open_positions = _open_positions(client, signal_type)

    for row in active_rows:
        symbol = str(row.get("symbol", "")).upper()
        price = row.get("price")
        if not symbol or not isinstance(price, (int, float)):
            continue

        current_price = float(price)
        return_pct = 0.0

        if symbol in open_positions:
            position_id, entry_price, _entry_date = open_positions[symbol]
            return_pct = ((current_price - entry_price) / entry_price) * 100 if entry_price else 0.0
            client.execute(
                """
                UPDATE trade_positions
                SET last_price = ?, return_pct = ?, updated_at = ?
                WHERE id = ?
                """,
                [current_price, return_pct, _now_iso(), position_id],
            )
            updated += 1
            continue

        client.execute(
            """
            INSERT INTO trade_positions (
              signal_type, symbol, entry_date, entry_price, last_price, return_pct, status, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'open', ?)
            """,
            [signal_type, symbol, snapshot_date, current_price, current_price, 0.0, _now_iso()],
        )
        opened += 1

    for symbol, (position_id, entry_price, _entry_date) in open_positions.items():
        if symbol in active_symbols:
            continue

        exit_price = price_lookup.get(symbol, entry_price)
        return_pct = ((exit_price - entry_price) / entry_price) * 100 if entry_price else 0.0
        client.execute(
            """
            UPDATE trade_positions
            SET status = 'closed',
                exit_date = ?,
                exit_price = ?,
                last_price = ?,
                return_pct = ?,
                updated_at = ?
            WHERE id = ?
            """,
            [snapshot_date, exit_price, exit_price, return_pct, _now_iso(), position_id],
        )
        closed += 1

    return opened, updated, closed


def sync_ready_to_buy_signals(
    *,
    snapshot_date: str | None = None,
    data: PulseData | None = None,
    client: libsql_client.Client | None = None,
) -> SyncResult:
    target_date = snapshot_date or _today()
    if data is None:
        cookies = require_session_cookies(base_url=BASE_URL)
        payload = fetch_pulse_data(base_url=BASE_URL, cookies=cookies)
    else:
        payload = data
    regime = get_regime(payload)
    pulse_updated_at = payload.get("updatedAt")

    momentum_rows = extract_momentum_rows(payload, ready_to_buy=True)
    quant_rows = extract_quant_score_rows(payload, ready_to_buy=True)
    price_lookup = _price_lookup(payload)

    owns_client = client is None
    db = client or create_client()
    try:
        init_schema(db)
        snapshot_id = _insert_snapshot(
            db,
            snapshot_date=target_date,
            regime=regime,
            pulse_updated_at=str(pulse_updated_at) if pulse_updated_at else None,
        )
        _insert_daily_signals(db, snapshot_id=snapshot_id, signal_type="momentum", rows=momentum_rows)
        _insert_daily_signals(db, snapshot_id=snapshot_id, signal_type="quant", rows=quant_rows)

        m_open, m_update, m_close = _update_trade_positions(
            db,
            signal_type="momentum",
            snapshot_date=target_date,
            active_rows=momentum_rows,
            price_lookup=price_lookup,
        )
        q_open, q_update, q_close = _update_trade_positions(
            db,
            signal_type="quant",
            snapshot_date=target_date,
            active_rows=quant_rows,
            price_lookup=price_lookup,
        )
    finally:
        if owns_client:
            db.close()

    return SyncResult(
        snapshot_date=target_date,
        regime=regime,
        momentum_count=len(momentum_rows),
        quant_count=len(quant_rows),
        opened_positions=m_open + q_open,
        updated_positions=m_update + q_update,
        closed_positions=m_close + q_close,
    )
