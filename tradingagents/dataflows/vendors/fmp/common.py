import os

import requests

from tradingagents.dataflows.errors import VendorNotConfiguredError, VendorUnavailableError
from tradingagents.dataflows.net import get_scrubbed

API_BASE_URL = "https://financialmodelingprep.com/stable"
REQUEST_TIMEOUT = 30


class FMPNotConfiguredError(VendorNotConfiguredError):
    pass


def get_api_key() -> str:
    api_key = os.getenv("FMP_API_KEY")
    if not api_key:
        raise FMPNotConfiguredError("FMP_API_KEY environment variable is not set.")
    return api_key


def make_api_request(endpoint: str, params: dict) -> list | dict:
    api_key = get_api_key()
    try:
        response = get_scrubbed(
            f"{API_BASE_URL}/{endpoint}",
            params={**params, "apikey": api_key},
            timeout=REQUEST_TIMEOUT,
            secret=api_key,
        )
    except requests.RequestException as exc:
        raise VendorUnavailableError(f"FMP request failed: {exc}") from None

    try:
        payload = response.json()
    except ValueError:
        raise VendorUnavailableError("FMP returned an invalid JSON response.") from None

    if isinstance(payload, dict):
        message = payload.get("Error Message") or payload.get("error") or payload.get("message")
        if message:
            safe_message = str(message).replace(api_key, "***")
            raise VendorUnavailableError(f"FMP rejected the request: {safe_message}")
    if not isinstance(payload, (list, dict)):
        raise VendorUnavailableError("FMP returned an unexpected response format.")
    return payload
