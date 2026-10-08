import json
from datetime import UTC, datetime
from unittest import mock

import pytest

from tradingagents.dataflows import router
from tradingagents.dataflows.config import set_config
from tradingagents.dataflows.errors import VendorNotConfiguredError
from tradingagents.dataflows.vendors.finnhub import common, news


def test_finnhub_company_news_requests_once_and_normalizes_response(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "test-secret")
    now = int(datetime.now(UTC).timestamp())
    request = mock.Mock(return_value=[
        {
            "datetime": now,
            "headline": "Company update",
            "source": "Example News",
            "summary": "Details",
            "url": "https://news.example/article",
        },
    ])
    monkeypatch.setattr(news, "make_api_request", request)
    monkeypatch.setattr(news, "get_config", lambda: {"news_article_limit": 20})

    result = json.loads(news.get_news("tsla", "2020-01-01", "2099-01-01"))

    request.assert_called_once_with(
        "company-news", {"symbol": "TSLA", "from": "2020-01-01", "to": "2099-01-01"}
    )
    assert result[0]["title"] == "Company update"
    assert result[0]["publisher"] == "Example News"
    assert result[0]["url"] == "https://news.example/article"
    assert result[0]["publishedDate"]


def test_finnhub_global_news_uses_market_news_endpoint_and_date_window(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "test-secret")
    now = int(datetime.now(UTC).timestamp())
    request = mock.Mock(return_value=[
        {"datetime": now, "headline": "Market update", "source": "Example News"},
    ])
    monkeypatch.setattr(news, "make_api_request", request)
    monkeypatch.setattr(
        news,
        "get_config",
        lambda: {"global_news_lookback_days": 7, "global_news_article_limit": 10},
    )

    result = json.loads(news.get_global_news(datetime.now(UTC).date().isoformat()))

    request.assert_called_once_with("news", {"category": "general", "minId": 0})
    assert result[0]["title"] == "Market update"


def test_finnhub_api_key_is_required(monkeypatch):
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)

    with pytest.raises(VendorNotConfiguredError, match="FINNHUB_API_KEY"):
        common.get_api_key()


def test_finnhub_api_request_adds_key_and_scrubs_it(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "test-secret")
    response = mock.Mock(status_code=200)
    response.json.return_value = []
    request = mock.Mock(return_value=response)
    monkeypatch.setattr(common, "get_scrubbed", request)

    assert common.make_api_request("news", {"category": "general"}) == []
    request.assert_called_once_with(
        "https://finnhub.io/api/v1/news",
        params={"category": "general", "token": "test-secret"},
        timeout=30,
        secret="test-secret",
        passthrough=(429,),
    )


def test_router_merges_finnhub_and_alpha_vantage_ticker_news(monkeypatch):
    now = datetime.now(UTC)
    end_date = now.date().isoformat()
    published = int(now.timestamp())
    finnhub_result = [{
        "datetime": published,
        "headline": "Shared company update",
        "source": "Example News",
        "summary": "Finnhub summary",
        "url": "https://news.example/shared",
    }]
    alpha_result = {
        "feed": [{
            "title": "Shared company update",
            "summary": "Alpha summary",
            "source": "Example News",
            "url": "https://news.example/shared",
            "time_published": now.strftime("%Y%m%dT%H%M%S"),
        }],
    }
    set_config({"data_vendors": {"news_data": "finnhub,alpha_vantage"}})
    monkeypatch.setitem(
        router.VENDOR_METHODS["get_news"], "finnhub", lambda *args: json.dumps(finnhub_result)
    )
    monkeypatch.setitem(
        router.VENDOR_METHODS["get_news"], "alpha_vantage", lambda *args: alpha_result
    )

    result = json.loads(
        router.route_to_vendor("get_news", "TSLA", end_date, end_date)
    )

    assert result["sources"] == ["Finnhub", "Alpha Vantage"]
    assert result["article_count"] == 1
    assert result["articles"][0]["source_vendors"] == ["Finnhub", "Alpha Vantage"]
    assert result["articles"][0]["summary"] == "Finnhub summary"


def test_router_merges_finnhub_global_news(monkeypatch):
    now = datetime.now(UTC)
    as_of = now.date().isoformat()
    set_config({"data_vendors": {"news_data": "finnhub,alpha_vantage"}})
    monkeypatch.setitem(
        router.VENDOR_METHODS["get_global_news"],
        "finnhub",
        lambda *args: json.dumps([{
            "datetime": int(now.timestamp()),
            "headline": "Global update",
            "source": "Example News",
            "url": "https://news.example/global",
            "publishedDate": now.isoformat(),
        }]),
    )
    monkeypatch.setitem(
        router.VENDOR_METHODS["get_global_news"],
        "alpha_vantage",
        lambda *args: {"feed": []},
    )

    result = json.loads(router.route_to_vendor("get_global_news", as_of, 7, 10))

    assert result["sources"] == ["Finnhub", "Alpha Vantage"]
    assert result["article_count"] == 1
    assert result["articles"][0]["source_vendor"] == "Finnhub"
