#!/usr/bin/env python3
"""Call the four Futu data interfaces implemented by TradingAgents."""

from __future__ import annotations

import argparse
import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, datetime, timedelta
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tradingagents.dataflows.vendors.futu import (  # noqa: E402
    get_capital_distribution,
    get_capital_flow,
    get_history_kline,
    get_market_snapshot,
)


APIS = (
    "capital-flow",
    "capital-distribution",
    "market-snapshot",
    "history-kline",
)


def _iso_date(value: str) -> str:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc


def _window(args: argparse.Namespace) -> tuple[str, str]:
    if bool(args.start_date) != bool(args.end_date):
        raise ValueError("--start-date and --end-date must be provided together")
    if args.start_date:
        start, end = args.start_date, args.end_date
    else:
        end = args.as_of_date
        start = (datetime.strptime(end, "%Y-%m-%d").date() - timedelta(days=args.look_back_days)).isoformat()
    if start > end:
        raise ValueError("start date must not be after end date")
    return start, end


def _call(api: str, args: argparse.Namespace) -> str:
    if api == "capital-distribution":
        return get_capital_distribution(args.symbol, args.as_of_date)
    if api == "market-snapshot":
        return get_market_snapshot(args.symbol, args.as_of_date)
    start, end = _window(args)
    if api == "capital-flow":
        return get_capital_flow(args.symbol, start, end)
    return get_history_kline(args.symbol, start, end)


def _result(api: str, args: argparse.Namespace) -> dict[str, str]:
    try:
        # futu-api writes connection lifecycle messages to the console. Keep
        # stdout machine-readable: callers need the interface payload, not SDK
        # diagnostics interleaved with CSV/JSON.
        sdk_console = io.StringIO()
        with redirect_stdout(sdk_console), redirect_stderr(sdk_console):
            output = _call(api, args)
    except Exception as exc:  # Preserve vendor exception type and text for callers.
        return {
            "status": "error",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
    status = "withheld" if output.startswith("FUTU_LIVE_DATA_WITHHELD") else "success"
    return {"status": status, "output": output}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch TradingAgents' Futu capital-flow, snapshot, or daily K-line data."
    )
    parser.add_argument("--symbol", required=True, help="US symbol (TSLA) or Yahoo-style HK symbol (0700.HK)")
    parser.add_argument("--api", required=True, choices=(*APIS, "all"))
    parser.add_argument("--as-of-date", type=_iso_date, default=date.today().isoformat())
    parser.add_argument("--look-back-days", type=int, default=30)
    parser.add_argument("--start-date", type=_iso_date)
    parser.add_argument("--end-date", type=_iso_date)
    args = parser.parse_args()
    if args.look_back_days < 0:
        parser.error("--look-back-days must be non-negative")

    if args.api != "all":
        result = _result(args.api, args)
        if result["status"] == "error":
            print(json.dumps(result, ensure_ascii=False, indent=2), file=sys.stderr)
            return 1
        print(result["output"])
        return 0

    results = {api: _result(api, args) for api in APIS}
    print(
        json.dumps(
            {"symbol": args.symbol, "as_of_date": args.as_of_date, "results": results},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if all(result["status"] == "error" for result in results.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
