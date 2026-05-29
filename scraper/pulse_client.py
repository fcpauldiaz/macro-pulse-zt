from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypedDict

import httpx

BASE_URL = "https://macro-wrap.vercel.app"
PULSE_DATA_PATH = "/api/pulse/data"
PULSE_CHART_PATH = "/api/pulse/chart"

REQUIRED_TOP_LEVEL_KEYS = frozenset(
    {
        "current",
        "history",
        "stocksByEtf",
        "momentumCandidates",
        "momentumReadySignals",
        "momentumSignals",
        "quantScoreSignals",
        "updatedAt",
    }
)


class PulseDataError(Exception):
    pass


class CurrentRegime(TypedDict, total=False):
    date: str
    signalDate: str
    displayDate: str
    score: float
    regime: str
    components: dict[str, Any]
    componentMode: str
    componentTitle: str


class PulseData(TypedDict, total=False):
    current: CurrentRegime
    history: list[dict[str, Any]]
    stocksByEtf: dict[str, list[str]]
    momentumCandidates: list[dict[str, Any]]
    momentumReadySignals: list[dict[str, Any]]
    momentumSignals: list[dict[str, Any]]
    quantScoreSignals: list[dict[str, Any]]
    momentumCandidatesUpdatedAt: str
    momentumReadySignalsUpdatedAt: str
    momentumUpdatedAt: str
    quantScoreUpdatedAt: str
    updatedAt: str
    snapshotMeta: dict[str, Any]
    snapshotErrors: dict[str, Any]
    snapshotStale: bool


@dataclass(frozen=True)
class PulseChart:
    symbol: str
    data: dict[str, Any]


def validate_pulse_data(payload: dict[str, Any]) -> PulseData:
    missing = REQUIRED_TOP_LEVEL_KEYS - payload.keys()
    if missing:
        raise PulseDataError(f"Missing required keys: {sorted(missing)}")

    current = payload.get("current")
    if not isinstance(current, dict):
        raise PulseDataError("'current' must be an object")

    for list_key in (
        "history",
        "momentumCandidates",
        "momentumReadySignals",
        "momentumSignals",
        "quantScoreSignals",
    ):
        value = payload.get(list_key)
        if not isinstance(value, list):
            raise PulseDataError(f"'{list_key}' must be a list")

    stocks = payload.get("stocksByEtf")
    if not isinstance(stocks, dict):
        raise PulseDataError("'stocksByEtf' must be an object")

    return payload  # type: ignore[return-value]


def extract_symbols(data: PulseData) -> list[str]:
    symbols: set[str] = set()

    for signal_list in ("momentumSignals", "momentumReadySignals", "quantScoreSignals"):
        for item in data.get(signal_list, []):
            symbol = item.get("symbol") if isinstance(item, dict) else None
            if isinstance(symbol, str) and symbol:
                symbols.add(symbol.upper())

    return sorted(symbols)


def fetch_pulse_data(
    *,
    base_url: str = BASE_URL,
    timeout: float = 30.0,
    client: httpx.Client | None = None,
) -> PulseData:
    url = f"{base_url.rstrip('/')}{PULSE_DATA_PATH}"

    if client is None:
        with httpx.Client(timeout=timeout, follow_redirects=True) as session:
            response = session.get(url)
    else:
        response = client.get(url)

    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise PulseDataError("Expected JSON object from /api/pulse/data")

    return validate_pulse_data(payload)


class PulseClient:
    def __init__(
        self,
        *,
        base_url: str = BASE_URL,
        timeout: float = 30.0,
        cookies: dict[str, str] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._cookies = cookies or {}

    @property
    def cookies(self) -> dict[str, str]:
        return dict(self._cookies)

    def with_cookies(self, cookies: dict[str, str]) -> PulseClient:
        return PulseClient(
            base_url=self.base_url,
            timeout=self.timeout,
            cookies=cookies,
        )

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.base_url,
            timeout=self.timeout,
            cookies=self._cookies,
            follow_redirects=False,
        )

    def fetch_data(self) -> PulseData:
        with self._client() as client:
            return fetch_pulse_data(base_url=self.base_url, timeout=self.timeout, client=client)

    def fetch_chart(self, symbol: str) -> PulseChart:
        normalized = symbol.strip().upper()
        if not normalized:
            raise PulseDataError("Symbol must not be empty")

        with self._client() as client:
            response = client.get(PULSE_CHART_PATH, params={"symbol": normalized})

        if response.status_code in (401, 307):
            raise PulseDataError(
                f"Unauthorized fetching chart for {normalized}; Clerk session required or expired"
            )

        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise PulseDataError(f"Expected JSON object for chart {normalized}")

        return PulseChart(symbol=normalized, data=payload)

    def fetch_charts(self, symbols: list[str]) -> list[PulseChart]:
        charts: list[PulseChart] = []
        for symbol in symbols:
            charts.append(self.fetch_chart(symbol))
        return charts

    def save_json(self, payload: dict[str, Any], output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
