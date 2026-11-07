import csv
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from geoflow.config import load_config
from geoflow.exceptions import QualityError
from geoflow.ingestion.readers import ingest
from geoflow.orchestration.pipeline import run_dataset
from geoflow.validation.quality import validate

ROOT = Path(__file__).resolve().parents[2]


def settings():
    return load_config(ROOT / "configs/quality.yml").datasets[0]


def test_corrupt_fixture_exact_dispositions_and_reconciliation():
    cfg = settings()
    content = cfg.path.read_bytes()
    records = ingest(content, cfg)
    result = validate(records, cfg)
    assert result.accepted["id"].tolist() == ["valid"]
    assert [r["row_id"] for r in result.rejected] == list(range(2, 11))
    assert result.summary.input_records == 10
    assert result.summary.accepted_records == 1
    assert result.summary.quarantined_records == 9
    assert result.summary.pass_rate == 0.1
    assert len(result.accepted) + len(result.rejected) == len(records)
    assert result.summary.rule_counts["custom_count_integer"] == 1
    assert result.summary.rule_counts["custom_demo_bounds"] == 3
    by_row = {r["row_id"]: r for r in result.rejected}
    assert by_row[3]["record"]["geometry"] == records[2].raw["geometry"]
    assert not records[2].geometry.is_valid
    assert len([i for i in result.issues if i.row_id == 9]) >= 2
    expected = {
        2: {"null_geometry", "custom_demo_bounds", "invalid_geometry_encoding"},
        3: {"invalid_geometry"},
        4: {"duplicate_id", "duplicate_record"},
        5: {"duplicate_id", "duplicate_record"},
        6: {"required_field"},
        7: {"custom_count_integer"},
        8: {"custom_demo_bounds"},
        9: {"custom_demo_bounds", "coordinates_out_of_range"},
        10: {"null_value"},
    }
    assert {row: {i.code for i in result.issues if i.row_id == row} for row in expected} == expected
    assert cfg.path.read_bytes() == content
    rerun = validate(records, cfg)
    assert rerun.summary == result.summary
    assert rerun.issues == result.issues


def test_reports_include_provenance_and_all_issues(tmp_path):
    path = run_dataset(settings(), tmp_path)
    report = json.loads(path.read_text())
    quality = json.loads((path.parent / "quality.json").read_text())
    with (path.parent / "issues.csv").open(newline="", encoding="utf-8") as stream:
        issues = list(csv.DictReader(stream))
    assert len(issues) == len(quality["issues"])
    assert quality["run_id"] == report["run_id"]
    assert quality["source"]["sha256"] == report["source_sha256"]
    assert quality["summary"]["input_records"] == 10
    assert len((path.parent / "quarantine.jsonl").read_text().splitlines()) == 9
    assert all(row["run_id"] == report["run_id"] for row in issues)


def custom_settings(severity="WARNING", action="report"):
    cfg = settings()
    values = cfg.model_dump()
    values["quality_rules"] = [
        {
            "code": "custom_advisory",
            "kind": "range",
            "field": "count",
            "parameters": {"min": 5},
            "severity": severity,
            "action": action,
        }
    ]
    return type(cfg)(**values)


@pytest.mark.parametrize("severity", ["INFO", "WARNING", "ERROR"])
def test_report_only_rule_does_not_quarantine_valid_control(severity):
    cfg = custom_settings(severity)
    record = ingest(cfg.path.read_bytes(), cfg)[0]
    result = validate([record], cfg)
    assert len(result.accepted) == 1 and not result.rejected
    assert result.summary.pass_rate == 0
    assert result.issues[0].code == "custom_advisory"
    assert result.issues[0].severity == severity


def test_warning_can_quarantine_and_fatal_cannot_be_report_only():
    for severity in ["WARNING", "FATAL"]:
        cfg = custom_settings(severity, "quarantine" if severity == "WARNING" else "report")
        result = validate(ingest(cfg.path.read_bytes(), cfg)[:1], cfg)
        assert len(result.rejected) == 1
        assert result.summary.has_fatal == (severity == "FATAL")


def test_fatal_persists_reports_and_prevents_database_load(tmp_path, monkeypatch):
    cfg = custom_settings("FATAL", "quarantine")
    cfg.sink = "postgis"
    called = []
    monkeypatch.setattr("geoflow.loading.postgis.load_postgis", lambda *a, **k: called.append(True))
    with pytest.raises(QualityError, match="Fatal"):
        run_dataset(cfg, tmp_path)
    path = next(tmp_path.rglob("quality.json"))
    assert json.loads(path.read_text())["summary"]["has_fatal"]
    assert (path.parent / "quarantine.jsonl").exists()
    assert called == []


def test_bad_rule_parameters_rejected():
    cfg = settings().model_dump()
    cfg["quality_rules"] = [{"code": "custom_bad", "kind": "bounds", "parameters": {"min_x": 1}}]
    with pytest.raises(ValidationError):
        type(settings())(**cfg)


def test_quality_gate_failure_still_publishes_complete_evidence(tmp_path):
    cfg = settings()
    cfg.max_invalid_fraction = 0
    with pytest.raises(QualityError):
        run_dataset(cfg, tmp_path)
    quality_path = next(tmp_path.rglob("quality.json"))
    quality = json.loads(quality_path.read_text())
    assert quality["summary"]["quarantined_records"] == 9
    assert (quality_path.parent / "source.geojson").read_bytes() == cfg.path.read_bytes()
    assert (quality_path.parent / "issues.csv").exists()
