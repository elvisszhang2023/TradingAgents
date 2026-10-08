"""Tests for the deterministic market-data verification snapshot (#830/#881)."""

from __future__ import annotations

import pandas as pd
import pytest
from unittest import mock

import tradingagents.dataflows.vendors.yahoo.snapshot as validator
from tradingagents.dataflows.errors import NoMarketDataError, VendorUnavailableError


def _sample_ohlcv() -> pd.DataFrame:
    dates = pd.bdate_range("2026-04-01", "2026-05-20")
    closes = [100 + i for i in range(len(dates))]
    return pd.DataFrame({
        "Date": dates,
        "Open": [c - 0.5 for c in closes],
        "High": [c + 1.0 for c in closes],
        "Low": [c - 1.0 for c in closes],
        "Close": closes,
        "Volume": [1_000_000 + i for i in range(len(dates))],
    })


def _use_routed_ohlcv(monkeypatch, data):
    monkeypatch.setattr(
        validator,
        "get_config",
        lambda: {
            "tool_vendors": {},
            "data_vendors": {"core_stock_apis": "fmp,alpha_vantage,yfinance"},
        },
    )
    monkeypatch.setattr(validator, "_load_routed_ohlcv", lambda *args: data)


@pytest.mark.unit
class TestVerifiedSnapshot:
    def test_excludes_future_rows(self, monkeypatch):
        data = pd.concat([
            _sample_ohlcv(),
            pd.DataFrame({"Date": [pd.Timestamp("2026-06-01")], "Open": [999.0],
                          "High": [999.0], "Low": [999.0], "Close": [999.0], "Volume": [999]}),
        ], ignore_index=True)
        _use_routed_ohlcv(monkeypatch, data)

        snap = validator.build_verified_market_snapshot("COF", "2026-05-13")
        assert "Verified market data snapshot for COF" in snap
        assert "Requested analysis date: 2026-05-13" in snap
        assert "Latest trading row used: 2026-05-13" in snap
        assert "999.00" not in snap          # future row excluded
        assert "boll_lb" in snap             # indicators present

    def test_uses_previous_trading_day_when_date_is_weekend(self, monkeypatch):
        _use_routed_ohlcv(monkeypatch, _sample_ohlcv())
        # 2026-05-16 is a Saturday; latest row should be Fri 2026-05-15
        snap = validator.build_verified_market_snapshot("COF", "2026-05-16")
        assert "Latest trading row used: 2026-05-15" in snap
        assert "Recent verified closes" in snap

    def test_raises_when_no_rows_on_or_before_date(self, monkeypatch):
        _use_routed_ohlcv(monkeypatch, _sample_ohlcv())
        with pytest.raises(NoMarketDataError):
            validator.build_verified_market_snapshot("COF", "2020-01-01")

    def test_raises_on_empty_data(self, monkeypatch):
        _use_routed_ohlcv(monkeypatch, pd.DataFrame())
        with pytest.raises(NoMarketDataError):
            validator.build_verified_market_snapshot("COF", "2026-05-13")

    def test_uses_configured_multi_source_price_route(self, monkeypatch):
        monkeypatch.setattr(
            validator,
            "get_config",
            lambda: {
                "tool_vendors": {},
                "data_vendors": {"core_stock_apis": "yfinance,fmp"},
            },
        )
        routed = _sample_ohlcv()
        routed_loader = mock.Mock(return_value=routed)
        monkeypatch.setattr(validator, "_load_routed_ohlcv", routed_loader)

        snapshot = validator.build_verified_market_snapshot("COF", "2026-05-20")

        assert "Latest trading row used: 2026-05-20" in snapshot
        routed_loader.assert_called_once_with("COF", "2026-05-20")

    def test_look_back_window_capped_at_30(self, monkeypatch):
        _use_routed_ohlcv(monkeypatch, _sample_ohlcv())
        snap = validator.build_verified_market_snapshot("COF", "2026-05-20", look_back_days=999)
        # last-N closes table has at most 30 data rows
        close_rows = [ln for ln in snap.splitlines() if ln.startswith("| 2026-")]
        assert 0 < len(close_rows) <= 30


@pytest.mark.unit
class TestTool:
    def test_tool_delegates_to_builder(self, monkeypatch):
        from tradingagents.agents.tools import get_verified_market_snapshot
        _use_routed_ohlcv(monkeypatch, _sample_ohlcv())
        out = get_verified_market_snapshot.invoke(
            {"symbol": "COF", "curr_date": "2026-05-20"}
        )
        assert "Verified market data snapshot for COF" in out
