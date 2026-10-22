from __future__ import annotations

import json
import os
from datetime import date, timedelta
from unittest import mock

import pandas as pd
import pytest

from tradingagents.agents import tools
from tradingagents.dataflows.errors import NoMarketDataError
from tradingagents.dataflows.vendors import futu


@pytest.mark.unit
@pytest.mark.parametrize(
    ("symbol", "expected"),
    [
        ("TSLA", ("US.TSLA", "US", "USD")),
        ("9999.HK", ("HK.09999", "HK", "HKD")),
        ("0700.hk", ("HK.00700", "HK", "HKD")),
    ],
)
def test_normalize_symbol(symbol, expected):
    assert futu.normalize_symbol(symbol) == expected


@pytest.mark.unit
@pytest.mark.parametrize("symbol", ["AAPL.L", "HK.00700", "BAD/HK", "123456.HK"])
def test_normalize_symbol_rejects_unsupported_forms(symbol):
    with pytest.raises(NoMarketDataError):
        futu.normalize_symbol(symbol)


def _snapshot_row(**overrides):
    fields = futu._COMMON_SNAPSHOT_FIELDS + futu._HK_SNAPSHOT_FIELDS + futu._US_SNAPSHOT_FIELDS
    row = {field: None for field in fields}
    row.update(
        {
            "last_price": 75.0,
            "prev_close_price": 70.0,
            "lowest52weeks_price": 50.0,
            "highest52weeks_price": 100.0,
            "equity_valid": True,
        }
    )
    row.update(overrides)
    return pd.DataFrame([row])


@pytest.mark.unit
def test_hk_snapshot_keeps_core_fields_and_derives_price_position():
    with mock.patch.object(futu, "_call", return_value=("HK.09999", _snapshot_row(), [])):
        result = json.loads(futu.get_market_snapshot("9999.HK"))

    data = result["data"]
    assert result["currency"] == "HKD"
    assert data["change_value"] == 5.0
    assert data["change_rate"] == pytest.approx(7.142857)
    assert data["position_52w"] == 50.0
    assert "lot_size" in data and "price_spread" in data
    assert "pre_price" not in data


@pytest.mark.unit
def test_us_snapshot_keeps_extended_hours_fields():
    frame = _snapshot_row(pre_price=74.0, after_price=76.0, overnight_price=75.5)
    with mock.patch.object(futu, "_call", return_value=("US.TSLA", frame, [])):
        result = json.loads(futu.get_market_snapshot("TSLA"))

    data = result["data"]
    assert result["currency"] == "USD"
    assert data["pre_price"] == 74.0
    assert data["after_price"] == 76.0
    assert data["overnight_price"] == 75.5
    assert "lot_size" not in data


@pytest.mark.unit
def test_snapshot_safe_math_returns_null():
    frame = _snapshot_row(prev_close_price=0.0, lowest52weeks_price=50.0, highest52weeks_price=50.0)
    with mock.patch.object(futu, "_call", return_value=("US.TSLA", frame, [])):
        data = json.loads(futu.get_market_snapshot("TSLA"))["data"]
    assert data["change_rate"] is None
    assert data["position_52w"] is None


@pytest.mark.unit
def test_capital_distribution_adds_net_amounts():
    frame = pd.DataFrame(
        [
            {
                "capital_in_super": 10.0,
                "capital_in_big": 8.0,
                "capital_in_mid": 6.0,
                "capital_in_small": 4.0,
                "capital_out_super": 3.0,
                "capital_out_big": 2.0,
                "capital_out_mid": 5.0,
                "capital_out_small": 7.0,
                "update_time": "2026-10-22 10:00:00",
            }
        ]
    )
    with mock.patch.object(futu, "_call", return_value=("HK.09999", frame, [])):
        data = json.loads(futu.get_capital_distribution("9999.HK"))["data"]
    assert data["net_super"] == 7.0
    assert data["net_big"] == 6.0
    assert data["net_main"] == 13.0
    assert data["net_total"] == 11.0


@pytest.mark.unit
@pytest.mark.parametrize("function", [futu.get_market_snapshot, futu.get_capital_distribution])
def test_live_data_is_withheld_from_historical_runs(function):
    with mock.patch.object(futu, "_call") as call:
        result = function("TSLA", "2026-01-01")
    assert result.startswith("FUTU_LIVE_DATA_WITHHELD")
    call.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize(
    "tool",
    [
        tools.get_futu_capital_flow,
        tools.get_futu_capital_distribution,
        tools.get_futu_market_snapshot,
        tools.get_futu_history_kline,
    ],
    ids=lambda tool: tool.name,
)
def test_futu_tool_hides_instrument_and_trade_date(tool):
    properties = tool.tool_call_schema.model_json_schema()["properties"]
    assert "symbol" not in properties
    assert "trade_date" not in properties


@pytest.mark.unit
def test_futu_tools_remain_separate_from_primary_vendor_chain():
    from tradingagents.dataflows.router import TOOLS_CATEGORIES, VENDOR_METHODS

    assert "futu" not in VENDOR_METHODS["get_stock_data"]
    assert TOOLS_CATEGORIES["capital_flow_data"]["tools"] == [
        "get_futu_capital_flow",
        "get_futu_capital_distribution",
        "get_futu_market_snapshot",
        "get_futu_history_kline",
    ]


@pytest.mark.unit
def test_disabled_futu_degrades_without_touching_primary_chain():
    from tradingagents.dataflows.config import run_config
    from tradingagents.dataflows.router import route_to_vendor

    with run_config({"futu_enabled": False}):
        result = route_to_vendor("get_futu_market_snapshot", "TSLA", "2026-10-22")
    assert result.startswith("DATA_UNAVAILABLE: optional capital_flow_data")


@pytest.mark.integration
@pytest.mark.parametrize("symbol", ["TSLA", "9999.HK"])
def test_live_futu_four_interface_contract(symbol):
    if os.environ.get("FUTU_ENABLED", "").lower() not in {"1", "true", "yes", "on"}:
        pytest.skip("set FUTU_ENABLED=true and start a logged-in Futu OpenD")

    from tradingagents.dataflows.router import route_to_vendor

    end = date.today().isoformat()
    start = (date.today() - timedelta(days=30)).isoformat()
    calls = [
        ("get_futu_capital_flow", (symbol, start, end)),
        ("get_futu_capital_distribution", (symbol, end)),
        ("get_futu_market_snapshot", (symbol, end)),
        ("get_futu_history_kline", (symbol, start, end)),
    ]
    for method, args in calls:
        result = route_to_vendor(method, *args)
        assert not result.startswith(("DATA_UNAVAILABLE", "NO_DATA_AVAILABLE")), result
        assert len(result) > 100
