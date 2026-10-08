"""Finnhub company and market news endpoints."""

import json
from datetime import UTC, datetime, timedelta

from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.errors import VendorUnavailableError
from tradingagents.dataflows.vendors.finnhub.common import make_api_request


def _normalize_articles(rows: list, start_date: str, end_date: str, limit: int) -> str:
    start = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=UTC)
    end = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=UTC) + timedelta(days=1)
    articles = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        timestamp = row.get("datetime")
        if not isinstance(timestamp, (int, float)):
            continue
        published = datetime.fromtimestamp(timestamp, UTC)
        if not start <= published < end:
            continue
        article = dict(row)
        article["title"] = row.get("headline", "")
        article["publisher"] = row.get("source", "Finnhub")
        article["publishedDate"] = published.isoformat()
        articles.append(article)

    articles.sort(key=lambda item: item["publishedDate"], reverse=True)
    if not articles:
        raise VendorUnavailableError("Finnhub returned no news in the requested date range.")
    return json.dumps(articles[:limit], ensure_ascii=False)


def get_news(ticker: str, start_date: str, end_date: str) -> str:
    """Fetch and normalize Finnhub company news for the requested date range."""
    datetime.strptime(start_date, "%Y-%m-%d")
    datetime.strptime(end_date, "%Y-%m-%d")
    rows = make_api_request(
        "company-news",
        {"symbol": ticker.strip().upper(), "from": start_date, "to": end_date},
    )
    return _normalize_articles(
        rows, start_date, end_date, int(get_config()["news_article_limit"])
    )


def get_global_news(
    as_of_date: str,
    look_back_days: int | None = None,
    limit: int | None = None,
) -> str:
    """Fetch recent general market news; Finnhub provides the latest feed page."""
    config = get_config()
    if look_back_days is None:
        look_back_days = config["global_news_lookback_days"]
    limit = config["global_news_article_limit"] if limit is None else limit
    end = datetime.strptime(as_of_date, "%Y-%m-%d").date()
    start_date = (end - timedelta(days=look_back_days)).isoformat()
    rows = make_api_request("news", {"category": "general", "minId": 0})
    return _normalize_articles(rows, start_date, as_of_date, int(limit))
