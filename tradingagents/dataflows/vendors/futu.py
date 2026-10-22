"""Optional Futu OpenAPI enrichment for market and capital-flow analysis.

Futu is deliberately separate from the OHLCV vendor chain: it adds capital
flow and live snapshot context, but never replaces Yahoo/FMP/Alpha Vantage.
The SDK is imported lazily so users without OpenD or ``futu-api`` keep the
existing analysis path unchanged.
"""

from __future__ import annotations

import json
import socket
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.date_window import is_historical
from tradingagents.dataflows.errors import (
    NoMarketDataError,
    VendorError,
    VendorNotConfiguredError,
    VendorUnavailableError,
)

_COMMON_SNAPSHOT_FIELDS = (
    "code",
    "name",
    "update_time",
    "sec_status",
    "suspension",
    "equity_valid",
    "last_price",
    "prev_close_price",
    "open_price",
    "high_price",
    "low_price",
    "avg_price",
    "volume",
    "turnover",
    "turnover_rate",
    "volume_ratio",
    "amplitude",
    "bid_price",
    "ask_price",
    "bid_vol",
    "ask_vol",
    "highest52weeks_price",
    "lowest52weeks_price",
    "total_market_val",
    "pe_ttm_ratio",
    "pb_ratio",
    "dividend_ratio_ttm",
)
_HK_SNAPSHOT_FIELDS = ("lot_size", "price_spread")
_US_SNAPSHOT_FIELDS = (
    "pre_price",
    "pre_change_rate",
    "pre_volume",
    "after_price",
    "after_change_rate",
    "after_volume",
    "overnight_price",
    "overnight_change_rate",
    "overnight_volume",
)


def _load_futu():
    try:
        import futu
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise VendorNotConfiguredError(
            'Futu enrichment requires the optional dependency: pip install "tradingagents[futu]"'
        ) from exc
    return futu


def _settings() -> tuple[str, int, float]:
    config = get_config()
    if not config.get("futu_enabled", False):
        raise VendorNotConfiguredError(
            "Futu enrichment is disabled; set FUTU_ENABLED=true after starting and logging in to OpenD"
        )
    return (
        str(config.get("futu_opend_host", "127.0.0.1")),
        int(config.get("futu_opend_port", 11111)),
        float(config.get("futu_connect_timeout_seconds", 10)),
    )


@contextmanager
def _quote_context():
    host, port, timeout = _settings()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
    except OSError as exc:
        raise VendorUnavailableError(f"Futu OpenD is unavailable at {host}:{port}: {exc}") from exc

    futu = _load_futu()
    try:
        context = futu.OpenQuoteContext(host=host, port=port)
    except Exception as exc:
        raise VendorUnavailableError(f"Could not connect to Futu OpenD at {host}:{port}: {exc}") from exc
    try:
        yield futu, context
    finally:
        try:
            context.close()
        except Exception:
            pass


def normalize_symbol(symbol: str) -> tuple[str, str, str]:
    """Return ``(Futu code, market, currency)`` for the supported US/HK forms."""
    raw = str(symbol).strip().upper()
    if raw.endswith(".HK"):
        digits = raw[:-3]
        if not digits.isdigit() or not (1 <= len(digits) <= 5):
            raise NoMarketDataError(symbol, detail="Hong Kong symbols must look like 0700.HK")
        return f"HK.{int(digits):05d}", "HK", "HKD"
    if "." not in raw and raw and all(char.isalnum() or char in "-" for char in raw):
        return f"US.{raw}", "US", "USD"
    raise NoMarketDataError(
        symbol,
        detail="Futu enrichment currently supports US symbols and Yahoo-style Hong Kong symbols",
    )


def _call(symbol: str, operation):
    canonical, _market, _currency = normalize_symbol(symbol)
    try:
        with _quote_context() as (futu, context):
            ret, data, *extra = operation(futu, context, canonical)
    except VendorError:
        raise
    except Exception as exc:
        raise VendorUnavailableError(f"Futu request failed for {canonical}: {exc}") from exc
    if ret != futu.RET_OK:
        raise VendorUnavailableError(f"Futu request failed for {canonical}: {data}")
    if isinstance(data, pd.DataFrame) and data.empty:
        raise NoMarketDataError(symbol, canonical, "Futu returned no rows")
    return canonical, data, extra


def _scalar(value: Any) -> Any:
    if value is None or (not isinstance(value, (list, dict, tuple)) and pd.isna(value)):
        return None
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat(sep=" ")
    return value


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)


def _live_withheld(symbol: str, as_of_date: str | None, dataset: str) -> str | None:
    if not is_historical(as_of_date):
        return None
    return (
        f"FUTU_LIVE_DATA_WITHHELD: {dataset} for {symbol} is live-only and cannot be used "
        f"in a historical analysis dated {as_of_date}. Proceed with the original historical sources."
    )


def get_capital_flow(symbol: str, start_date: str, end_date: str) -> str:
    canonical, market, currency = normalize_symbol(symbol)

    def operation(futu, context, code):
        return context.get_capital_flow(
            code,
            period_type=futu.PeriodType.DAY,
            start=start_date,
            end=end_date,
        )

    _canonical, data, _extra = _call(symbol, operation)
    return (
        f"# Futu historical capital flow for {symbol}\n"
        f"# Canonical: {canonical}; market: {market}; currency: {currency}; "
        f"window: {start_date}..{end_date}\n"
        "# Positive values are inferred net inflow; main_in_flow is super + big orders. "
        "This does not identify an institution.\n"
        + data.to_csv(index=False)
    )


def get_capital_distribution(symbol: str, as_of_date: str | None = None) -> str:
    withheld = _live_withheld(symbol, as_of_date, "capital distribution")
    if withheld:
        return withheld
    canonical, market, currency = normalize_symbol(symbol)
    _canonical, data, _extra = _call(
        symbol, lambda _futu, context, code: context.get_capital_distribution(code)
    )
    row = {key: _scalar(value) for key, value in data.iloc[-1].to_dict().items()}
    for size in ("super", "big", "mid", "small"):
        row[f"net_{size}"] = row[f"capital_in_{size}"] - row[f"capital_out_{size}"]
    row["net_main"] = row["net_super"] + row["net_big"]
    row["net_total"] = sum(row[f"net_{size}"] for size in ("super", "big", "mid", "small"))
    return _json(
        {
            "source": "futu",
            "dataset": "live_capital_distribution",
            "symbol": symbol,
            "canonical_symbol": canonical,
            "market": market,
            "currency": currency,
            "semantics": "Order-size inferred inflow/outflow; not confirmed institutional trading.",
            "data": row,
        }
    )


def _ratio(numerator: Any, denominator: Any) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator


def get_market_snapshot(symbol: str, as_of_date: str | None = None) -> str:
    withheld = _live_withheld(symbol, as_of_date, "market snapshot")
    if withheld:
        return withheld
    canonical, market, currency = normalize_symbol(symbol)
    _canonical, data, _extra = _call(
        symbol, lambda _futu, context, code: context.get_market_snapshot([code])
    )
    row = data.iloc[-1]
    fields = _COMMON_SNAPSHOT_FIELDS + (_HK_SNAPSHOT_FIELDS if market == "HK" else _US_SNAPSHOT_FIELDS)
    values = {field: _scalar(row[field]) if field in row.index else None for field in fields}
    last_price = values["last_price"]
    previous_close = values["prev_close_price"]
    low_52w = values["lowest52weeks_price"]
    high_52w = values["highest52weeks_price"]
    change_value = None if last_price is None or previous_close is None else last_price - previous_close
    change_rate = _ratio(change_value, previous_close)
    position_52w = None
    if last_price is not None and low_52w is not None and high_52w is not None:
        position_52w = _ratio(last_price - low_52w, high_52w - low_52w)
    values.update(
        {
            "change_value": change_value,
            "change_rate": None if change_rate is None else change_rate * 100,
            "position_52w": None if position_52w is None else position_52w * 100,
        }
    )
    return _json(
        {
            "source": "futu",
            "dataset": "live_market_snapshot",
            "symbol": symbol,
            "canonical_symbol": canonical,
            "market": market,
            "currency": currency,
            "data": values,
        }
    )


def get_history_kline(symbol: str, start_date: str, end_date: str) -> str:
    canonical, market, currency = normalize_symbol(symbol)

    def operation(futu, context, code):
        frames: list[pd.DataFrame] = []
        page_key = None
        while True:
            ret, frame, page_key = context.request_history_kline(
                code,
                start=start_date,
                end=end_date,
                ktype=futu.KLType.K_DAY,
                autype=futu.AuType.QFQ,
                fields=[futu.KL_FIELD.ALL],
                max_count=1000,
                page_req_key=page_key,
            )
            if ret != futu.RET_OK:
                return ret, frame, page_key
            frames.append(frame)
            if page_key is None:
                break
        combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        if not combined.empty:
            combined = combined.drop_duplicates(subset=["time_key"]).sort_values("time_key")
        return futu.RET_OK, combined, None

    _canonical, data, _extra = _call(symbol, operation)
    return (
        f"# Futu daily history for {symbol}\n"
        f"# Canonical: {canonical}; market: {market}; currency: {currency}; "
        f"window: {start_date}..{end_date}; adjustment: forward-adjusted (qfq)\n"
        + data.to_csv(index=False)
    )


__all__ = [
    "get_capital_distribution",
    "get_capital_flow",
    "get_history_kline",
    "get_market_snapshot",
    "normalize_symbol",
]
