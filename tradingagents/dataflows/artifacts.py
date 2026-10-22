"""Run-scoped, model-independent market-data artifacts.

Vendor responses are captured at the router boundary, before an LLM can
summarise or discard fields.  A completed run writes the exact routed payload,
a machine-friendly normalised copy where possible, and a manifest describing
every vendor attempt (including fallbacks and errors).
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from dataclasses import dataclass
from datetime import datetime
from io import StringIO
from pathlib import Path
from time import perf_counter
from typing import Any

import pandas as pd


_CSV_METHODS = {
    "get_stock_data",
    "get_futu_capital_flow",
    "get_futu_history_kline",
}
_FRIENDLY_NAMES = {
    "get_futu_capital_flow": "capital_flow",
    "get_futu_capital_distribution": "capital_distribution",
    "get_futu_market_snapshot": "market_snapshot",
    "get_futu_history_kline": "history_kline",
}
_SECRET = re.compile(
    r"(?i)(api[_-]?key|access[_-]?token|token|password|secret)(=|%3D)([^&\s]+)"
)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _redact_text(value: str) -> str:
    return _SECRET.sub(lambda match: f"{match.group(1)}{match.group(2)}REDACTED", value)


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return _redact_text(value) if isinstance(value, str) else value
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        secret_keys = {
            "api_key", "apikey", "access_key", "access_token", "token",
            "password", "secret", "authorization",
        }
        return {
            str(key): "REDACTED"
            if str(key).lower() in secret_keys
            else _json_safe(item)
            for key, item in value.items()
        }
    return _redact_text(str(value))


def _raw_payload(result: Any, method: str) -> tuple[str, str]:
    if isinstance(result, pd.DataFrame):
        return result.to_csv(index=False), "csv"
    if isinstance(result, (dict, list)):
        return json.dumps(_json_safe(result), ensure_ascii=False, indent=2), "json"
    text = _redact_text(str(result))
    stripped = text.lstrip()
    if stripped.startswith(("{", "[")):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            pass
        else:
            return json.dumps(_json_safe(parsed), ensure_ascii=False, indent=2), "json"
    return text, "csv" if method in _CSV_METHODS else "txt"


def _normalised_payload(result: Any, method: str, normalized: Any = None) -> tuple[str, str] | None:
    value = normalized if normalized is not None else result
    if isinstance(value, pd.DataFrame):
        return value.to_csv(index=False), "csv"
    if isinstance(value, (dict, list)):
        return json.dumps(_json_safe(value), ensure_ascii=False, indent=2), "json"
    if not isinstance(value, str):
        return None
    stripped = value.lstrip()
    if stripped.startswith(("{", "[")):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return json.dumps(_json_safe(parsed), ensure_ascii=False, indent=2), "json"
    if method not in _CSV_METHODS:
        return None
    try:
        frame = pd.read_csv(StringIO(value), comment="#")
    except (pd.errors.ParserError, ValueError):
        return None
    if frame.empty or len(frame.columns) < 2:
        return None
    return frame.to_csv(index=False), "csv"


def _provenance(result: Any) -> dict[str, Any]:
    payload: Any = result
    if isinstance(result, str) and result.lstrip().startswith(("{", "[")):
        try:
            payload = json.loads(result)
        except json.JSONDecodeError:
            payload = None
    if isinstance(payload, dict):
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        return {
            key: value
            for key, value in {
                "canonical_symbol": payload.get("canonical_symbol"),
                "market": payload.get("market"),
                "currency": payload.get("currency"),
                "data_as_of": data.get("update_time"),
            }.items()
            if value is not None
        }
    if isinstance(result, str):
        metadata: dict[str, Any] = {}
        for key, pattern in {
            "canonical_symbol": r"Canonical:\s*([^;\n]+)",
            "market": r"market:\s*([^;\n]+)",
            "currency": r"currency:\s*([^;\n]+)",
        }.items():
            match = re.search(pattern, result, re.IGNORECASE)
            if match:
                metadata[key] = match.group(1).strip()
        return metadata
    return {}


@dataclass
class _Attempt:
    entry: dict[str, Any]
    raw: tuple[str, str] | None
    normalized: tuple[str, str] | None


class DataArtifactCollector:
    """Thread-safe collection of vendor calls made by one analysis run."""

    def __init__(self, ticker: str, analysis_date: str) -> None:
        self.ticker = ticker
        self.analysis_date = analysis_date
        self.started_at = _now()
        self._lock = threading.Lock()
        self._attempts: list[_Attempt] = []

    def __deepcopy__(self, _memo):
        # Run configs are intentionally deep-copied into worker contexts.  The
        # collector must remain shared so parallel analyst/tool calls land in
        # one manifest; its own mutation is protected by ``_lock``.
        return self

    @staticmethod
    def timer() -> float:
        return perf_counter()

    def record(
        self,
        *,
        method: str,
        vendor: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        started_at: str,
        started_clock: float,
        status: str,
        result: Any = None,
        normalized: Any = None,
        error: Exception | None = None,
    ) -> None:
        raw = _raw_payload(result, method) if result is not None else None
        normal = _normalised_payload(result, method, normalized) if result is not None else None
        entry = {
            "source": vendor,
            "api": method,
            "status": status,
            "requested_at": started_at,
            "completed_at": _now(),
            "latency_ms": round((perf_counter() - started_clock) * 1000, 3),
            "args": _json_safe(args),
            "kwargs": _json_safe(kwargs),
        }
        entry.update(_provenance(result))
        if error is not None:
            entry["error_type"] = type(error).__name__
            entry["error"] = _redact_text(str(error))
        with self._lock:
            self._attempts.append(_Attempt(entry, raw, normal))

    def write(self, report_dir: str | Path) -> Path:
        root = Path(report_dir) / "data"
        raw_root = root / "raw"
        normalized_root = root / "normalized"
        raw_root.mkdir(parents=True, exist_ok=True)
        normalized_root.mkdir(parents=True, exist_ok=True)
        with self._lock:
            attempts = list(self._attempts)

        counts: dict[tuple[str, str], int] = {}
        entries: list[dict[str, Any]] = []
        for attempt in attempts:
            entry = dict(attempt.entry)
            vendor = re.sub(r"[^A-Za-z0-9_.-]+", "_", entry["source"])
            base = _FRIENDLY_NAMES.get(entry["api"], entry["api"])
            key = (vendor, base)
            counts[key] = counts.get(key, 0) + 1
            suffix = "" if counts[key] == 1 else f"__{counts[key]:03d}"

            if attempt.raw is not None:
                text, extension = attempt.raw
                relative = Path("raw") / vendor / f"{base}{suffix}.{extension}"
                destination = root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(text, encoding="utf-8")
                entry["raw_file"] = relative.as_posix()
                entry["sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
            elif entry["status"] != "success":
                relative = Path("raw") / vendor / f"{base}{suffix}_error.json"
                destination = root / relative
                error_payload = json.dumps(
                    {key: entry[key] for key in ("status", "error_type", "error") if key in entry},
                    ensure_ascii=False,
                    indent=2,
                )
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(error_payload, encoding="utf-8")
                entry["raw_file"] = relative.as_posix()

            if attempt.normalized is not None:
                text, extension = attempt.normalized
                relative = Path("normalized") / f"{vendor}__{base}{suffix}.{extension}"
                (root / relative).write_text(text, encoding="utf-8")
                entry["normalized_file"] = relative.as_posix()
            entries.append(entry)

        manifest = {
            "schema_version": 1,
            "ticker": self.ticker,
            "analysis_date": self.analysis_date,
            "capture_started_at": self.started_at,
            "generated_at": _now(),
            "attempt_count": len(entries),
            "attempts": entries,
        }
        manifest_path = root / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return manifest_path


__all__ = ["DataArtifactCollector"]
