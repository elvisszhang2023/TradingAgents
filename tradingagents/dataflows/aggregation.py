import json
import re
from datetime import datetime
from io import StringIO
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pandas as pd

from tradingagents.dataflows.date_window import in_window
from tradingagents.dataflows.errors import NoMarketDataError, VendorUnavailableError

VENDOR_LABELS = {
    "alpha_vantage": "Alpha Vantage",
    "finnhub": "Finnhub",
    "fmp": "Financial Modeling Prep",
    "yfinance": "Yahoo Finance",
}


def parse_ohlcv_result(
    vendor: str,
    result: str,
    symbol: str,
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    if not isinstance(result, str):
        raise VendorUnavailableError(
            f"{VENDOR_LABELS.get(vendor, vendor)} returned non-text OHLCV data."
        )
    if result.startswith("DATA_UNAVAILABLE"):
        raise VendorUnavailableError(result)
    if result.startswith("NO_DATA_AVAILABLE"):
        raise NoMarketDataError(symbol, symbol, result)

    try:
        frame = pd.read_csv(StringIO(result), comment="#")
    except (pd.errors.ParserError, ValueError):
        raise VendorUnavailableError(
            f"{VENDOR_LABELS.get(vendor, vendor)} returned invalid OHLCV data."
        ) from None
    if frame.empty:
        raise NoMarketDataError(symbol, symbol, f"no {vendor} rows in requested range")

    columns = {str(column).strip().lower(): column for column in frame.columns}
    date_column = columns.get("date") or columns.get("timestamp")
    required = ("open", "high", "low", "close", "volume")
    if date_column is None or any(column not in columns for column in required):
        raise VendorUnavailableError(
            f"{VENDOR_LABELS.get(vendor, vendor)} returned incomplete OHLCV fields."
        )

    normalized = frame.rename(
        columns={
            date_column: "Date",
            **{columns[column]: column.title() for column in required},
        }
    )
    normalized["Date"] = pd.to_datetime(normalized["Date"], errors="coerce")
    for column in ("Open", "High", "Low", "Close", "Volume"):
        normalized[column] = pd.to_numeric(normalized[column], errors="coerce")
    normalized = normalized.dropna(subset=["Date", "Open", "High", "Low", "Close", "Volume"])
    normalized = normalized[
        (normalized["Date"] >= pd.Timestamp(start_date))
        & (normalized["Date"] <= pd.Timestamp(end_date))
    ]
    if normalized.empty:
        raise NoMarketDataError(symbol, symbol, f"no {vendor} rows in requested range")
    return normalized


def _parse_yahoo_news(text: str) -> tuple[list[dict], str | None]:
    articles = []
    current = None

    def append_current():
        if current and current["title"]:
            articles.append({
                "title": current["title"],
                "summary": "\n".join(current["summary"]).strip(),
                "publisher": current["publisher"],
                "url": current["url"],
            })

    for line in text.splitlines():
        if line.startswith("### "):
            append_current()
            heading = line[4:].strip()
            match = re.match(r"^(.*?)\s+\(source:\s*(.*?)\)$", heading)
            current = {
                "title": match.group(1) if match else heading,
                "publisher": match.group(2) if match else "Yahoo Finance",
                "url": "",
                "summary": [],
            }
        elif current and line.startswith("Link: "):
            current["url"] = line[6:].strip()
        elif current and line.strip() and not line.startswith("## "):
            current["summary"].append(line.strip())
    append_current()
    return articles, None if articles else text.strip() or None


def _news_payload(
    vendor: str,
    result,
    start_date: str,
    end_date: str,
) -> tuple[list[dict], dict | None, str | None]:
    if vendor == "yfinance" and isinstance(result, str):
        articles, message = _parse_yahoo_news(result)
        for article in articles:
            article["source_vendor"] = VENDOR_LABELS.get(vendor, vendor)
        return articles, None, message

    payload = result
    if isinstance(payload, str):
        if payload.startswith("DATA_UNAVAILABLE"):
            raise VendorUnavailableError(payload)
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            if vendor == "yfinance":
                articles, message = _parse_yahoo_news(payload)
                for article in articles:
                    article["source_vendor"] = VENDOR_LABELS.get(vendor, vendor)
                return articles, None, message
            raise VendorUnavailableError(f"{VENDOR_LABELS.get(vendor, vendor)} returned invalid news data.") from None

    if isinstance(payload, list):
        records, metadata = payload, None
    elif isinstance(payload, dict):
        records = payload.get("feed") or payload.get("articles") or []
        metadata = {key: value for key, value in payload.items() if key not in {"feed", "articles"}}
    else:
        raise VendorUnavailableError(f"{VENDOR_LABELS.get(vendor, vendor)} returned an unexpected news format.")

    if not isinstance(records, list):
        raise VendorUnavailableError(f"{VENDOR_LABELS.get(vendor, vendor)} returned an unexpected article list.")
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    articles = []
    for article in records:
        if isinstance(article, dict):
            published = _article_timestamp(article)
            if not in_window(published, start, end):
                continue
            normalized = dict(article)
            normalized["source_vendor"] = VENDOR_LABELS.get(vendor, vendor)
            articles.append(normalized)
    return articles, metadata, None


def _canonical_url(article: dict) -> str:
    url = article.get("url") or article.get("link") or ""
    if not isinstance(url, str) or not url:
        return ""
    parts = urlsplit(url.strip())
    query = urlencode(
        [
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if not key.lower().startswith("utm_")
            and key.lower() not in {"fbclid", "gclid", "ref", "source"}
        ]
    )
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), query, "")
    )


def _article_timestamp(article: dict) -> datetime | None:
    for field in ("publishedDate", "time_published", "published_at", "pub_date", "datetime"):
        value = article.get(field)
        if value:
            if field == "datetime" and isinstance(value, (int, float)):
                return pd.to_datetime(value, unit="s", utc=True).to_pydatetime()
            if field == "time_published" and isinstance(value, str):
                for date_format in ("%Y%m%dT%H%M%S", "%Y%m%dT%H%M"):
                    try:
                        return datetime.strptime(value, date_format)
                    except ValueError:
                        pass
            parsed = pd.to_datetime(value, errors="coerce")
            if not pd.isna(parsed):
                return parsed.to_pydatetime()
    return None


def _article_date(article: dict) -> str:
    published = _article_timestamp(article)
    return published.isoformat() if published else ""


def merge_news_results(results: list[tuple[str, list[dict], dict | None, str | None]]) -> str:
    articles = []
    index_by_key = {}
    metadata = {}
    messages = {}
    sources = []

    for vendor, vendor_articles, vendor_metadata, message in results:
        label = VENDOR_LABELS.get(vendor, vendor)
        sources.append(label)
        if vendor_metadata:
            metadata[label] = vendor_metadata
        if message:
            messages[label] = message
        for article in vendor_articles:
            title = str(article.get("title") or "").strip()
            url = _canonical_url(article)
            title_key = re.sub(r"\W+", "", title.casefold())
            if not title and not url:
                continue
            keys = []
            if url:
                keys.append(("url", url))
            if title_key:
                keys.append(("title", title_key))
            duplicate_index = next((index_by_key[key] for key in keys if key in index_by_key), None)
            if duplicate_index is not None:
                existing = articles[duplicate_index]
                existing_sources = existing.setdefault(
                    "source_vendors", [existing.get("source_vendor", "")]
                )
                if label not in existing_sources:
                    existing_sources.append(label)
                if title and title != existing.get("title"):
                    existing.setdefault("alternate_titles", []).append(title)
                for field, value in article.items():
                    if field not in existing:
                        existing[field] = value
                continue
            articles.append(article)
            for key in keys:
                index_by_key[key] = len(articles) - 1

    articles.sort(key=lambda article: _article_date(article), reverse=True)
    return json.dumps(
        {
            "sources": sources,
            "article_count": len(articles),
            "articles": articles,
            "source_metadata": metadata,
            "source_messages": messages,
        },
        ensure_ascii=False,
        default=str,
    )
