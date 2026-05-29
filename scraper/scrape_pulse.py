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
from scraper.pulse_client import PulseClient, PulseDataError, extract_symbols, fetch_pulse_data


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
    except (PulseDataError, ClerkLoginError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Unknown command: {args.command}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
