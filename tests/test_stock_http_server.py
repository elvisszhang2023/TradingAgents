from scripts.stock_http_server import AnalysisService


def test_report_paths_include_data_artifacts(tmp_path):
    (tmp_path / "complete_report.md").write_text("report", encoding="utf-8")
    (tmp_path / "complete_report.html").write_text("page", encoding="utf-8")
    (tmp_path / "data" / "raw").mkdir(parents=True)
    (tmp_path / "data" / "normalized").mkdir(parents=True)
    (tmp_path / "data" / "manifest.json").write_text("{}", encoding="utf-8")

    paths = AnalysisService._report_paths(f"Report saved to: {tmp_path}\n")

    assert paths["data_manifest"] == str(tmp_path / "data" / "manifest.json")
    assert paths["raw_data_dir"] == str(tmp_path / "data" / "raw")
    assert paths["normalized_data_dir"] == str(tmp_path / "data" / "normalized")


def test_include_raw_data_must_be_boolean():
    status, body = AnalysisService(timeout_seconds=1).analyze(
        {"ticker": "TSLA", "include_raw_data": "yes"}
    )

    assert status == 400
    assert body == {"success": False, "error": "include_raw_data must be a boolean"}
