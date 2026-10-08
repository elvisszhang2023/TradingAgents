import json
from datetime import datetime

import pandas as pd

from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.errors import NoMarketDataError, VendorUnavailableError
from tradingagents.dataflows.vendors.fmp.common import make_api_request

NEWS_PAGE_SIZE = 250
NEWS_MAX_PAGES = 100


def get_stock(symbol: str, start_date: str, end_date: str) -> str:
    datetime.strptime(start_date, "%Y-%m-%d")
    datetime.strptime(end_date, "%Y-%m-%d")
    rows = make_api_request(
        "historical-price-eod/full",
        {"symbol": symbol.strip().upper(), "from": start_date, "to": end_date},
    )
    if not isinstance(rows, list):
        raise VendorUnavailableError("FMP historical prices returned an unexpected response.")
    if not rows:
        raise NoMarketDataError(
            symbol,
            symbol,
            f"no FMP daily prices between {start_date} and {end_date}",
        )

    frame = pd.DataFrame(rows)
    required = {"date", "open", "high", "low", "close", "volume"}
    if not required.issubset(frame.columns):
        raise VendorUnavailableError("FMP historical prices are missing required OHLCV fields.")
    frame = frame.rename(
        columns={
            "date": "Date",
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "volume": "Volume",
        }
    )
    frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce")
    for column in ("Open", "High", "Low", "Close", "Volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["Open", "High", "Low", "Close", "Volume"])
    frame = frame.dropna(subset=["Date"])
    frame = frame[
        (frame["Date"] >= pd.Timestamp(start_date))
        & (frame["Date"] <= pd.Timestamp(end_date))
    ].sort_values("Date")
    if frame.empty:
        raise NoMarketDataError(
            symbol,
            symbol,
            f"no FMP daily prices between {start_date} and {end_date}",
        )
    frame["Date"] = frame["Date"].dt.strftime("%Y-%m-%d")
    return frame[["Date", "Open", "High", "Low", "Close", "Volume"]].to_csv(index=False)


def get_news(ticker: str, start_date: str, end_date: str) -> str:
    datetime.strptime(start_date, "%Y-%m-%d")
    datetime.strptime(end_date, "%Y-%m-%d")
    article_limit = min(
        NEWS_PAGE_SIZE * NEWS_MAX_PAGES,
        max(1, int(get_config()["news_article_limit"])),
    )
    page_size = min(NEWS_PAGE_SIZE, article_limit)
    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    filtered = []
    seen = set()
    for page in range(NEWS_MAX_PAGES):
        rows = make_api_request(
            "news/stock",
            {
                "symbols": ticker.strip().upper(),
                "from": start_date,
                "to": end_date,
                "page": page,
                "limit": page_size,
            },
        )
        if not isinstance(rows, list):
            raise VendorUnavailableError("FMP stock news returned an unexpected response.")

        for article in rows:
            if not isinstance(article, dict):
                continue
            published = pd.to_datetime(article.get("publishedDate"), errors="coerce")
            if pd.isna(published) or not start <= published.date() <= end:
                continue
            identity = article.get("url") or (
                article.get("title", ""), article.get("publishedDate", "")
            )
            if identity in seen:
                continue
            seen.add(identity)
            filtered.append(article)
            if len(filtered) >= article_limit:
                break

        if len(filtered) >= article_limit or len(rows) < page_size:
            break
    if not filtered:
        raise VendorUnavailableError(
            f"FMP returned no stock news for {ticker} in the requested date range."
        )
    return json.dumps(filtered, ensure_ascii=False)
