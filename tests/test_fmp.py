import json
from io import StringIO
from unittest import mock

import pandas as pd
import pytest

from tradingagents.dataflows.errors import NoMarketDataError, VendorNotConfiguredError
from tradingagents.dataflows.vendors.fmp import common, market


def test_fmp_historical_prices_are_normalized_sorted_and_date_bounded(monkeypatch):
    monkeypatch.setattr(
        market,
        "make_api_request",
        lambda endpoint, params: [
            {"date": "2026-01-12", "open": 12, "high": 13, "low": 11, "close": 12.5, "volume": 120},
            {"date": "2026-01-09", "open": 9, "high": 10, "low": 8, "close": 9.5, "volume": 90},
            {"date": "2026-01-08", "open": 8, "high": 9, "low": 7, "close": 8.5, "volume": 80},
        ],
    )

    result = market.get_stock("tsla", "2026-01-09", "2026-01-11")
    frame = pd.read_csv(StringIO(result))

    assert list(frame.columns) == ["Date", "Open", "High", "Low", "Close", "Volume"]
    assert frame["Date"].tolist() == ["2026-01-09"]


def test_fmp_historical_prices_raise_no_data_for_empty_range(monkeypatch):
    monkeypatch.setattr(market, "make_api_request", lambda *args, **kwargs: [])

    with pytest.raises(NoMarketDataError):
        market.get_stock("TSLA", "2026-01-09", "2026-01-10")


def test_fmp_stock_news_is_date_bounded_and_uses_article_limit(monkeypatch):
    monkeypatch.setenv("FMP_API_KEY", "test-secret")
    monkeypatch.setattr(market, "get_config", lambda: {"news_article_limit": 50})
    articles = [
        {"publishedDate": "2026-01-10 15:00:00", "title": "in range"},
        {"publishedDate": "2026-01-11 00:00:01", "title": "future"},
        {"publishedDate": "not-a-date", "title": "undated"},
    ]
    request = mock.Mock(return_value=articles)
    monkeypatch.setattr(market, "make_api_request", request)

    result = json.loads(market.get_news("tsla", "2026-01-09", "2026-01-10"))

    assert result == [articles[0]]
    request.assert_called_once_with(
        "news/stock",
        {"symbols": "TSLA", "from": "2026-01-09", "to": "2026-01-10", "page": 0, "limit": 50},
    )


def test_fmp_stock_news_fetches_additional_pages_only_to_meet_configured_limit(monkeypatch):
    monkeypatch.setattr(market, "get_config", lambda: {"news_article_limit": 300})
    first_page = [
        {
            "publishedDate": "2026-01-10 15:00:00",
            "title": f"article {index}",
            "url": f"https://news.test/{index}",
        }
        for index in range(250)
    ]
    second_page = [
        {
            "publishedDate": "2026-01-10 15:00:00",
            "title": f"article {index}",
            "url": f"https://news.test/{index}",
        }
        for index in range(250, 300)
    ]
    request = mock.Mock(side_effect=[first_page, second_page])
    monkeypatch.setattr(market, "make_api_request", request)

    result = json.loads(market.get_news("TSLA", "2026-01-09", "2026-01-10"))

    assert len(result) == 300
    assert request.call_count == 2
    assert request.call_args_list[0].args[0] == "news/stock"
    assert request.call_args_list[0].args[1]["page"] == 0
    assert request.call_args_list[0].args[1]["limit"] == 250
    assert request.call_args_list[1].args[1]["page"] == 1
    assert request.call_args_list[1].args[1]["limit"] == 250


def test_fmp_key_is_required(monkeypatch):
    monkeypatch.delenv("FMP_API_KEY", raising=False)

    with pytest.raises(VendorNotConfiguredError, match="FMP_API_KEY"):
        common.get_api_key()
