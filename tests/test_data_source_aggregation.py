import json
from unittest import mock

from tradingagents.dataflows.config import run_config
from tradingagents.dataflows.router import VENDOR_METHODS, route_to_vendor


def test_stock_news_merges_configured_sources_without_repeating_successful_requests():
    calls = {"fmp": 0, "alpha_vantage": 0, "yfinance": 0}

    def fmp_news(*args, **kwargs):
        calls["fmp"] += 1
        return json.dumps([
            {
                "title": "Tesla expands its charging network",
                "url": "https://news.test/tesla-network?utm_source=fmp",
                "publishedDate": "2026-10-05 15:00:00",
                "site": "FMP Wire",
                "text": "FMP article details.",
            }
        ])

    def alpha_news(*args, **kwargs):
        calls["alpha_vantage"] += 1
        return json.dumps({
            "items": "2",
            "feed": [
                {
                    "title": "Tesla expands its charging network",
                    "url": "https://news.test/tesla-network",
                    "time_published": "20261005T150000",
                    "ticker_sentiment": [{"ticker": "TSLA", "sentiment_score": "0.7"}],
                },
                {
                    "title": "Analysts discuss Tesla deliveries",
                    "url": "https://news.test/tesla-deliveries",
                    "time_published": "20261004T120000",
                },
            ],
            "sentiment_score_definition": "provider metadata",
        })

    def yahoo_news(*args, **kwargs):
        calls["yfinance"] += 1
        return (
            "## TSLA News\n\n"
            "### Tesla owners report software update (source: Yahoo Finance)\n"
            "Yahoo article details.\n"
            "Link: https://news.test/tesla-update\n"
        )

    patched = {
        "get_news": {
            "fmp": fmp_news,
            "alpha_vantage": alpha_news,
            "yfinance": yahoo_news,
        }
    }
    with run_config({"data_vendors": {"news_data": "fmp,alpha_vantage,yfinance"}}):
        with mock.patch.dict(VENDOR_METHODS, patched):
            result = route_to_vendor("get_news", "TSLA", "2026-10-01", "2026-10-06")

    merged = json.loads(result)
    assert calls == {"fmp": 1, "alpha_vantage": 1, "yfinance": 1}
    assert merged["article_count"] == 3
    assert merged["sources"] == ["Financial Modeling Prep", "Alpha Vantage", "Yahoo Finance"]
    duplicate = merged["articles"][0]
    assert duplicate["source_vendors"] == ["Financial Modeling Prep", "Alpha Vantage"]
    assert duplicate["ticker_sentiment"]
    assert merged["source_metadata"]["Alpha Vantage"]["sentiment_score_definition"] == "provider metadata"
