#!/usr/bin/env python3
"""Local HTTP wrapper around scripts/analyze_stock.sh."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ANALYZE_SCRIPT = PROJECT_ROOT / "scripts" / "analyze_stock.sh"
TICKER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-^=]{0,19}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ALLOWED_ANALYSTS = {"market", "news", "fundamentals", "sentiment"}
DEFAULT_ANALYSTS = "market,news,fundamentals,sentiment"


class AnalysisService:
    def __init__(self, timeout_seconds: int) -> None:
        self.timeout_seconds = timeout_seconds
        self._lock = threading.Lock()

    def analyze(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        ticker = payload.get("ticker")
        if not isinstance(ticker, str) or not TICKER_RE.fullmatch(ticker.strip()):
            return self._error(HTTPStatus.BAD_REQUEST, "ticker must be a valid symbol")
        ticker = ticker.strip().upper()

        date = payload.get("date")
        if date is not None and (not isinstance(date, str) or not DATE_RE.fullmatch(date)):
            return self._error(HTTPStatus.BAD_REQUEST, "date must use YYYY-MM-DD format")

        analysts = payload.get("analysts", DEFAULT_ANALYSTS)
        if isinstance(analysts, list):
            analysts = ",".join(analysts)
        if not isinstance(analysts, str):
            return self._error(HTTPStatus.BAD_REQUEST, "analysts must be a comma-separated string")
        names = [name.strip().lower() for name in analysts.split(",") if name.strip()]
        if not names or any(name not in ALLOWED_ANALYSTS for name in names):
            return self._error(HTTPStatus.BAD_REQUEST, "analysts contains an unsupported analyst")

        include_raw_data = payload.get("include_raw_data", True)
        if not isinstance(include_raw_data, bool):
            return self._error(HTTPStatus.BAD_REQUEST, "include_raw_data must be a boolean")

        # Keep the HTTP worker available while waiting, but serialize the
        # expensive analysis subprocesses instead of rejecting concurrent
        # requests with HTTP 409.
        self._lock.acquire()
        try:
            # Always pass the script's three positional arguments explicitly.
            command = [
                str(ANALYZE_SCRIPT),
                ticker,
                date or dt.date.today().isoformat(),
                ",".join(names),
            ]
            environment = os.environ.copy()
            environment["TRADINGAGENTS_RESULTS_DIR"] = str(PROJECT_ROOT)
            environment["TRADINGAGENTS_INCLUDE_RAW_DATA"] = str(include_raw_data).lower()
            try:
                completed = subprocess.run(
                    command,
                    cwd=PROJECT_ROOT,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                return self._error(
                    HTTPStatus.GATEWAY_TIMEOUT,
                    "analysis timed out",
                    timeout_seconds=self.timeout_seconds,
                    stdout=self._text(exc.stdout),
                    stderr=self._text(exc.stderr),
                )
            except OSError as exc:
                return self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

            stdout = completed.stdout or ""
            stderr = completed.stderr or ""
            result: dict[str, Any] = {
                "success": completed.returncode == 0,
                "exit_code": completed.returncode,
                "stdout": stdout,
                "stderr": stderr,
            }
            result.update(self._report_paths(stdout))
            if completed.returncode != 0:
                return HTTPStatus.INTERNAL_SERVER_ERROR, result
            if not result.get("complete_report"):
                result["success"] = False
                result["error"] = "analysis completed without a report path"
                return HTTPStatus.INTERNAL_SERVER_ERROR, result
            return HTTPStatus.OK, result
        finally:
            self._lock.release()

    @staticmethod
    def _report_paths(stdout: str) -> dict[str, str]:
        match = re.search(r"Report saved to:\s*(.+)", stdout)
        if not match:
            return {}
        report_dir = Path(match.group(1).strip())
        result: dict[str, str] = {"report_dir": str(report_dir)}
        markdown = report_dir / "complete_report.md"
        html = report_dir / "complete_report.html"
        if markdown.is_file():
            result["complete_report"] = str(markdown)
        if html.is_file():
            result["complete_report_html"] = str(html)
        manifest = report_dir / "data" / "manifest.json"
        raw_data = report_dir / "data" / "raw"
        normalized_data = report_dir / "data" / "normalized"
        if manifest.is_file():
            result["data_manifest"] = str(manifest)
        if raw_data.is_dir():
            result["raw_data_dir"] = str(raw_data)
        if normalized_data.is_dir():
            result["normalized_data_dir"] = str(normalized_data)
        return result

    @staticmethod
    def _text(value: str | bytes | None) -> str:
        if value is None:
            return ""
        return value.decode(errors="replace") if isinstance(value, bytes) else value

    @staticmethod
    def _error(status: HTTPStatus, message: str, **extra: Any) -> tuple[int, dict[str, Any]]:
        body: dict[str, Any] = {"success": False, "error": message}
        body.update(extra)
        return status, body


class RequestHandler(BaseHTTPRequestHandler):
    service: AnalysisService

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/health":
            self._send(HTTPStatus.NOT_FOUND, {"success": False, "error": "not found"})
            return
        self._send(HTTPStatus.OK, {"success": True, "service": "stock-analysis", "status": "ok"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/analyze":
            self._send(HTTPStatus.NOT_FOUND, {"success": False, "error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
        except (ValueError, json.JSONDecodeError) as exc:
            self._send(HTTPStatus.BAD_REQUEST, {"success": False, "error": f"invalid JSON: {exc}"})
            return
        if not isinstance(payload, dict):
            self._send(HTTPStatus.BAD_REQUEST, {"success": False, "error": "JSON body must be an object"})
            return
        status, body = self.service.analyze(payload)
        self._send(status, body)

    def _send(self, status: int, body: dict[str, Any]) -> None:
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[{self.log_date_time_string}] {format % args}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Local TradingAgents HTTP service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args()

    if not ANALYZE_SCRIPT.is_file():
        raise SystemExit(f"analysis script not found: {ANALYZE_SCRIPT}")
    RequestHandler.service = AnalysisService(args.timeout)
    server = ThreadingHTTPServer((args.host, args.port), RequestHandler)
    print(f"stock-analysis HTTP service listening on http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
