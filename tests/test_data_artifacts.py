import json

from tradingagents.dataflows.artifacts import DataArtifactCollector
from tradingagents.dataflows.config import run_config
from tradingagents.dataflows.errors import VendorUnavailableError
from tradingagents.dataflows import router


def test_collector_writes_raw_normalized_manifest_and_errors(tmp_path):
    collector = DataArtifactCollector("TSLA", "2026-10-22")
    started = collector.timer()
    collector.record(
        method="get_futu_market_snapshot",
        vendor="futu",
        args=("TSLA",),
        kwargs={},
        started_at="2026-10-22T11:00:00+08:00",
        started_clock=started,
        status="success",
        result=json.dumps(
            {
                "canonical_symbol": "US.TSLA",
                "currency": "USD",
                "data": {"update_time": "2026-10-21 16:00:00", "last_price": 386.47},
            }
        ),
    )
    collector.record(
        method="get_stock_data",
        vendor="fmp",
        args=("TSLA", "2026-10-01", "2026-10-22"),
        kwargs={"api_key": "must-not-leak"},
        started_at="2026-10-22T11:00:01+08:00",
        started_clock=collector.timer(),
        status="error",
        error=RuntimeError("request failed?apikey=must-not-leak"),
    )

    manifest_path = collector.write(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["ticker"] == "TSLA"
    assert manifest["attempt_count"] == 2
    success, failure = manifest["attempts"]
    assert success["raw_file"] == "raw/futu/market_snapshot.json"
    assert success["normalized_file"] == "normalized/futu__market_snapshot.json"
    assert success["canonical_symbol"] == "US.TSLA"
    assert success["data_as_of"] == "2026-10-21 16:00:00"
    assert failure["kwargs"]["api_key"] == "REDACTED"
    assert "must-not-leak" not in json.dumps(manifest)
    assert (tmp_path / "data" / failure["raw_file"]).is_file()


def test_collector_preserves_csv_and_removes_metadata_from_normalized_copy(tmp_path):
    collector = DataArtifactCollector("TSLA", "2026-10-22")
    source = "# source metadata\nDate,Open,High,Low,Close,Volume\n2026-10-21,1,2,0.5,1.5,10\n"
    collector.record(
        method="get_stock_data",
        vendor="yfinance",
        args=("TSLA", "2026-10-21", "2026-10-22"),
        kwargs={},
        started_at="2026-10-22T11:00:00+08:00",
        started_clock=collector.timer(),
        status="success",
        result=source,
    )

    manifest = json.loads(collector.write(tmp_path).read_text(encoding="utf-8"))
    entry = manifest["attempts"][0]
    assert (tmp_path / "data" / entry["raw_file"]).read_text(encoding="utf-8") == source
    normalized = (tmp_path / "data" / entry["normalized_file"]).read_text(encoding="utf-8")
    assert normalized.startswith("Date,Open,High,Low,Close,Volume\n")
    assert "# source metadata" not in normalized


def test_router_captures_fallback_attempts(monkeypatch, tmp_path):
    collector = DataArtifactCollector("TSLA", "2026-10-22")
    csv = "Date,Open,High,Low,Close,Volume\n2026-10-21,1,2,0.5,1.5,10\n"

    def unavailable(*_args, **_kwargs):
        raise VendorUnavailableError("temporary failure")

    monkeypatch.setitem(router.VENDOR_METHODS["get_stock_data"], "fmp", unavailable)
    monkeypatch.setitem(router.VENDOR_METHODS["get_stock_data"], "yfinance", lambda *_a, **_k: csv)
    config = {
        "tool_vendors": {"get_stock_data": "fmp,yfinance"},
        "_data_artifact_collector": collector,
    }
    with run_config(config):
        assert router.route_to_vendor(
            "get_stock_data", "TSLA", "2026-10-21", "2026-10-22"
        ) == csv

    manifest = json.loads(collector.write(tmp_path).read_text(encoding="utf-8"))
    assert [attempt["source"] for attempt in manifest["attempts"]] == ["fmp", "yfinance"]
    assert [attempt["status"] for attempt in manifest["attempts"]] == [
        "unavailable",
        "success",
    ]
    assert manifest["attempts"][1]["normalized_file"].endswith(".csv")
