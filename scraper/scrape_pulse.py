#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from scraper.clerk_login import (
    DEFAULT_SESSION_PATH,
    ClerkLoginError,
    load_session_cookies,
    login_and_save_session,
)
from scraper.pulse_client import (
    PulseClient,
    PulseDataError,
    extract_signal_table,
    extract_symbols,
    fetch_pulse_data,
    get_regime,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scrape MacroPulse data from macro-wrap.vercel.app",
    )
    parser.add_argument(
        "--base-url",
        default="https://macro-wrap.vercel.app",
        help="Base URL for the MacroPulse deployment",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output"),
        help="Directory where JSON artifacts are written",
    )
    parser.add_argument(
        "--session-path",
        type=Path,
        default=DEFAULT_SESSION_PATH,
        help="Path to persisted Clerk session cookies",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    data_parser = subparsers.add_parser("data", help="Fetch public /api/pulse/data JSON")
    data_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output file (default: output/pulse_data.json)",
    )

    login_parser = subparsers.add_parser("login", help="Sign in with Clerk and save session cookies")
    login_parser.add_argument("--email", default=None, help="Subscriber email (or PULSE_EMAIL env var)")
    login_parser.add_argument(
        "--password",
        default=None,
        help="Subscriber password (or PULSE_PASSWORD env var)",
    )
    login_parser.add_argument(
        "--headed",
        action="store_true",
        help="Run browser in headed mode for debugging",
    )

    charts_parser = subparsers.add_parser(
        "charts",
        help="Fetch authenticated /api/pulse/chart data for signal symbols",
    )
    charts_parser.add_argument(
        "--symbols",
        nargs="*",
        default=None,
        help="Optional explicit symbol list; defaults to symbols from pulse data",
    )
    charts_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of chart requests",
    )
    charts_parser.add_argument(
        "--refresh-login",
        action="store_true",
        help="Re-authenticate with Clerk before fetching charts",
    )
    charts_parser.add_argument("--email", default=None, help="Subscriber email for refresh login")
    charts_parser.add_argument(
        "--password",
        default=None,
        help="Subscriber password for refresh login",
    )

    all_parser = subparsers.add_parser("all", help="Fetch pulse data and authenticated charts")
    all_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of chart requests",
    )
    all_parser.add_argument(
        "--refresh-login",
        action="store_true",
        help="Re-authenticate with Clerk before fetching charts",
    )
    all_parser.add_argument("--email", default=None, help="Subscriber email for refresh login")
    all_parser.add_argument(
        "--password",
        default=None,
        help="Subscriber password for refresh login",
    )

    signals_parser = subparsers.add_parser(
        "signals",
        help="Export Señales table rows (Growth / Momentum / Quant Score)",
    )
    signals_parser.add_argument(
        "--tab",
        choices=["growth", "momentum", "quantScore", "all"],
        default="momentum",
        help="Dashboard tab to export (default: momentum)",
    )
    signals_parser.add_argument(
        "--ready-to-buy",
        action="store_true",
        help="Apply Ready to Buy filter (Momentum + Quant Score tabs)",
    )
    signals_parser.add_argument(
        "--format",
        choices=["json", "csv"],
        default="json",
        help="Output format",
    )
    signals_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output file path",
    )

    return parser.parse_args()


def _resolve_credentials(args: argparse.Namespace) -> tuple[str, str]:
    import os

    email = args.email or os.getenv("PULSE_EMAIL", "")
    password = args.password or os.getenv("PULSE_PASSWORD", "")

    if not email or not password:
        raise ClerkLoginError(
            "Missing credentials. Set PULSE_EMAIL and PULSE_PASSWORD or pass --email/--password."
        )

    return email, password


def _ensure_session(args: argparse.Namespace) -> dict[str, str]:
    if args.refresh_login:
        email, password = _resolve_credentials(args)
        return login_and_save_session(
            email=email,
            password=password,
            session_path=args.session_path,
            base_url=args.base_url,
        )

    return load_session_cookies(args.session_path)


def _write_data(data: dict, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _cmd_data(args: argparse.Namespace) -> int:
    data = fetch_pulse_data(base_url=args.base_url)
    output_path = args.output or (args.output_dir / "pulse_data.json")
    _write_data(data, output_path)

    print(f"Saved pulse data to {output_path}")
    print(f"updatedAt={data.get('updatedAt')} snapshotStale={data.get('snapshotStale')}")
    return 0


def _cmd_login(args: argparse.Namespace) -> int:
    email, password = _resolve_credentials(args)
    cookies = login_and_save_session(
        email=email,
        password=password,
        session_path=args.session_path,
        base_url=args.base_url,
        headless=not args.headed,
    )
    print(f"Saved Clerk session to {args.session_path} ({len(cookies)} cookies)")
    return 0


def _fetch_charts(args: argparse.Namespace) -> tuple[dict, list[dict]]:
    cookies = _ensure_session(args)
    client = PulseClient(base_url=args.base_url, cookies=cookies)

    data = fetch_pulse_data(base_url=args.base_url)
    symbols = args.symbols or extract_symbols(data)
    if args.limit is not None:
        symbols = symbols[: args.limit]

    charts: list[dict] = []
    for symbol in symbols:
        chart = client.fetch_chart(symbol)
        charts.append({"symbol": chart.symbol, "data": chart.data})

    return data, charts


def _cmd_charts(args: argparse.Namespace) -> int:
    _, charts = _fetch_charts(args)
    output_path = args.output_dir / "pulse_charts.json"
    _write_data({"charts": charts}, output_path)
    print(f"Saved {len(charts)} charts to {output_path}")
    return 0



def _write_csv(rows: list[dict], output_path: Path) -> None:
    import csv

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        output_path.write_text("", encoding="utf-8")
        return

    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row.keys():
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)

    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _collect_signal_rows(data: dict, tab: str, ready_to_buy: bool) -> list[dict]:
    if tab == "all":
        rows: list[dict] = []
        rows.extend(extract_signal_table(data, "growth"))
        rows.extend(extract_signal_table(data, "momentum", ready_to_buy=ready_to_buy))
        rows.extend(extract_signal_table(data, "quantScore", ready_to_buy=ready_to_buy))
        return rows

    return extract_signal_table(data, tab, ready_to_buy=ready_to_buy)


def _default_signals_output(args: argparse.Namespace) -> Path:
    suffix = "csv" if args.format == "csv" else "json"
    if args.tab == "all":
        name = f"signals_all.{suffix}"
    elif args.tab == "momentum" and args.ready_to_buy:
        name = f"signals_momentum_ready_to_buy.{suffix}"
    else:
        name = f"signals_{args.tab}.{suffix}"
    return args.output_dir / name


def _cmd_signals(args: argparse.Namespace) -> int:
    data = fetch_pulse_data(base_url=args.base_url)
    rows = _collect_signal_rows(data, args.tab, args.ready_to_buy)
    output_path = args.output or _default_signals_output(args)

    if args.format == "csv":
        _write_csv(rows, output_path)
    else:
        payload = {
            "regime": get_regime(data),
            "tab": args.tab,
            "readyToBuy": args.ready_to_buy,
            "count": len(rows),
            "updatedAt": data.get("updatedAt"),
            "rows": rows,
        }
        _write_data(payload, output_path)

    print(f"Saved {len(rows)} rows to {output_path}")
    print(f"regime={get_regime(data)} tab={args.tab} readyToBuy={args.ready_to_buy}")
    return 0

def _cmd_all(args: argparse.Namespace) -> int:
    data, charts = _fetch_charts(args)

    data_path = args.output_dir / "pulse_data.json"
    charts_path = args.output_dir / "pulse_charts.json"

    _write_data(data, data_path)
    _write_data({"charts": charts}, charts_path)

    print(f"Saved pulse data to {data_path}")
    print(f"Saved {len(charts)} charts to {charts_path}")
    return 0


def main() -> int:
    load_dotenv()
    args = _parse_args()

    try:
        if args.command == "data":
            return _cmd_data(args)
        if args.command == "login":
            return _cmd_login(args)
        if args.command == "charts":
            return _cmd_charts(args)
        if args.command == "all":
            return _cmd_all(args)
        if args.command == "signals":
            return _cmd_signals(args)
    except (PulseDataError, ClerkLoginError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Unknown command: {args.command}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
