"""Vendor router must respect the configured chain and never silently hide a
broken primary.

Regressions for #988 (explicit single-vendor config still fell back to others),
#289 (fallback ran for unchosen vendors), and #989 (serious primary failures
were swallowed without a trace).
"""
import copy
from io import StringIO
import unittest
from unittest import mock

import pandas as pd
import pytest

import tradingagents.dataflows.config as config_module
import tradingagents.default_config as default_config
from tradingagents.dataflows import router
from tradingagents.dataflows.config import set_config
from tradingagents.dataflows.errors import NoMarketDataError


def _reset_config():
    # Hard reset: set_config() merges, so empty DEFAULT dicts (e.g. tool_vendors)
    # don't clear keys leaked by other tests. Replace the global outright.
    config_module._config = copy.deepcopy(default_config.DEFAULT_CONFIG)


def _no_data(symbol, *a, **k):
    raise NoMarketDataError(symbol, symbol, "no rows")


def _returns(value):
    def impl(symbol, *a, **k):
        return value
    return impl


def _raises(exc):
    def impl(symbol, *a, **k):
        raise exc
    return impl


def _prices(rows):
    return "Date,Open,High,Low,Close,Volume\n" + "\n".join(rows) + "\n"


@pytest.mark.unit
class VendorRoutingTests(unittest.TestCase):
    def setUp(self):
        _reset_config()

    def tearDown(self):
        _reset_config()

    def _route(self, vendors_for_get_stock_data):
        return mock.patch.dict(
            router.VENDOR_METHODS,
            {"get_stock_data": vendors_for_get_stock_data},
            clear=False,
        )

    def test_explicit_single_vendor_does_not_fall_back(self):
        # #988: with yfinance pinned, a healthy alpha_vantage must NOT be used.
        set_config({"data_vendors": {"core_stock_apis": "yfinance"}})
        av = mock.Mock(side_effect=_returns("AV_DATA"))
        with self._route({"yfinance": _no_data, "alpha_vantage": av}):
            result = router.route_to_vendor("get_stock_data", "FAKE", "2026-01-01", "2026-01-10")
        self.assertIn("NO_DATA_AVAILABLE", result)
        av.assert_not_called()  # the unchosen vendor was never tried

    def test_multi_vendor_chain_includes_usable_sources(self):
        set_config({"data_vendors": {"core_stock_apis": "yfinance,alpha_vantage"}})
        with self._route({"yfinance": _no_data, "alpha_vantage": _returns(_prices([
            "2026-01-05,10,11,9,10.5,100",
        ]))}):
            result = router.route_to_vendor("get_stock_data", "AAPL", "2026-01-01", "2026-01-10")
        self.assertIn("2026-01-05", result)

    def test_fmp_is_available_as_a_price_fallback(self):
        set_config({"data_vendors": {"core_stock_apis": "yfinance,fmp"}})
        with self._route({"yfinance": _no_data, "fmp": _returns(_prices([
            "2026-01-05,10,11,9,10.5,100",
        ]))}):
            result = router.route_to_vendor("get_stock_data", "TSLA", "2026-01-01", "2026-01-10")
        self.assertIn("2026-01-05", result)

    def test_price_data_uses_first_valid_source_without_calling_later_sources(self):
        set_config({"data_vendors": {"core_stock_apis": "fmp,alpha_vantage,yfinance"}})
        alpha = mock.Mock(return_value=_prices(["2026-01-06,20,21,19,20.5,200"]))
        yahoo = mock.Mock(return_value=_prices(["2026-01-07,30,31,29,30.5,300"]))
        fmp = mock.Mock(return_value=_prices(["2026-01-05,10,11,9,10.5,100"]))
        vendors = {
            "fmp": fmp,
            "alpha_vantage": alpha,
            "yfinance": yahoo,
        }
        with self._route(vendors):
            result = router.route_to_vendor("get_stock_data", "TSLA", "2026-01-01", "2026-01-10")

        frame = pd.read_csv(StringIO(result), comment="#")
        fmp.assert_called_once()
        alpha.assert_not_called()
        yahoo.assert_not_called()
        self.assertEqual(frame["Date"].tolist(), ["2026-01-05"])
        self.assertEqual(frame["Close"].tolist(), [10.5])

    def test_price_data_falls_back_when_primary_response_is_invalid(self):
        set_config({"data_vendors": {"core_stock_apis": "fmp,alpha_vantage,yfinance"}})
        fmp = mock.Mock(return_value="date,open,high,low,close\n2026-01-05,10,11,9,10.5\n")
        alpha = mock.Mock(return_value=_prices(["2026-01-06,20,21,19,20.5,200"]))
        yahoo = mock.Mock(return_value=_prices(["2026-01-07,30,31,29,30.5,300"]))
        with self._route({"fmp": fmp, "alpha_vantage": alpha, "yfinance": yahoo}):
            result = router.route_to_vendor("get_stock_data", "TSLA", "2026-01-01", "2026-01-10")

        frame = pd.read_csv(StringIO(result), comment="#")
        fmp.assert_called_once()
        alpha.assert_called_once()
        yahoo.assert_not_called()
        self.assertEqual(frame["Date"].tolist(), ["2026-01-06"])

    def test_primary_error_is_logged_not_masked(self):
        # #989: a broken primary is not hidden behind a fallback's verdict. It
        # never said whether it has the symbol, so the answer is unavailable,
        # and the failure is in the logs.
        set_config({"data_vendors": {"core_stock_apis": "yfinance,alpha_vantage"}})
        with self._route({"yfinance": _raises(ValueError("boom")), "alpha_vantage": _no_data}), \
                self.assertLogs("tradingagents.dataflows.router", level="WARNING") as cm:
            result = router.route_to_vendor("get_stock_data", "AAPL", "2026-01-01", "2026-01-10")
        self.assertTrue(result.startswith("DATA_UNAVAILABLE"), result)
        joined = "\n".join(cm.output)
        self.assertIn("boom", joined)            # the real error surfaced in logs
        self.assertIn("yfinance", joined)

    def test_unknown_configured_vendor_raises(self):
        set_config({"data_vendors": {"core_stock_apis": "bogus_vendor"}})
        with self.assertRaises(ValueError) as ctx:
            router.route_to_vendor("get_stock_data", "AAPL", "2026-01-01", "2026-01-10")
        self.assertIn("bogus_vendor", str(ctx.exception))

    def test_default_sentinel_uses_all_vendors(self):
        # No explicit choice ("default") keeps the resilient full-chain behavior.
        set_config({"data_vendors": {"core_stock_apis": "default"}})
        with self._route({"yfinance": _no_data, "alpha_vantage": _returns(_prices([
            "2026-01-05,10,11,9,10.5,100",
        ]))}):
            result = router.route_to_vendor("get_stock_data", "AAPL", "2026-01-01", "2026-01-10")
        self.assertIn("2026-01-05", result)

    def _route_method(self, method, vendors):
        return mock.patch.dict(router.VENDOR_METHODS, {method: vendors}, clear=False)

    def test_optional_category_degrades_instead_of_raising(self):
        # An optional enrichment vendor (FRED macro) that raises must NOT abort
        # the run — the router returns a sentinel so the analysis proceeds.
        set_config({"data_vendors": {"macro_data": "fred"}})
        with self._route_method(
            "get_macro_indicators", {"fred": _raises(ValueError("FRED 400: bad series"))}
        ):
            result = router.route_to_vendor("get_macro_indicators", "cpi", "2026-01-01")
        self.assertIn("DATA_UNAVAILABLE", result)
        self.assertIn("macro_data", result)

    def test_core_category_still_raises_on_error(self):
        # A core category (single configured vendor) propagates the error so a
        # broken primary is loud, not silently degraded.
        set_config({"data_vendors": {"core_stock_apis": "yfinance"}})
        with self._route({"yfinance": _raises(ValueError("boom"))}), \
                self.assertRaises(ValueError):
            router.route_to_vendor("get_stock_data", "AAPL", "2026-01-01", "2026-01-10")


if __name__ == "__main__":
    unittest.main()
