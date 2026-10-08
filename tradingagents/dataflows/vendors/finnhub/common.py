"""Shared Finnhub REST API helpers."""

import os

import requests

from tradingagents.dataflows.errors import VendorNotConfiguredError, VendorUnavailableError
from tradingagents.dataflows.net import get_scrubbed

API_BASE_URL = "https://finnhub.io/api/v1"
REQUEST_TIMEOUT = 30


def get_api_key() -> str:
    api_key = os.getenv("FINNHUB_API_KEY")
    if not api_key:
        raise VendorNotConfiguredError(
            "FINNHUB_API_KEY environment variable is not set."
        )
    return api_key


def make_api_request(endpoint: str, params: dict) -> list:
    api_key = get_api_key()
    try:
        response = get_scrubbed(
            f"{API_BASE_URL}/{endpoint.lstrip('/')}",
            params={**params, "token": api_key},
            timeout=REQUEST_TIMEOUT,
            secret=api_key,
            passthrough=(429,),
        )
    except requests.RequestException as exc:
        raise VendorUnavailableError(f"Finnhub request failed: {exc}") from None
    if response.status_code == 429:
        raise VendorUnavailableError("Finnhub API rate limit reached (HTTP 429).")
    try:
        payload = response.json()
    except ValueError:
        raise VendorUnavailableError("Finnhub returned invalid JSON.") from None
    if isinstance(payload, dict) and payload.get("error"):
        message = str(payload["error"]).replace(api_key, "***")
        raise VendorUnavailableError(f"Finnhub API error: {message}")
    if not isinstance(payload, list):
        raise VendorUnavailableError("Finnhub returned an unexpected response format.")
    return payload
