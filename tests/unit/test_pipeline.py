import hashlib
import json
from pathlib import Path

import geopandas as gpd
import pytest
from typer.testing import CliRunner

from geoflow.cli import app
from geoflow.config import Dataset
from geoflow.exceptions import IngestionError, QualityError, RunLockedError
from geoflow.orchestration.pipeline import run_dataset

ROOT = Path(__file__).resolve().parents[2]


def dataset(tmp_path: Path, source: str = "points.csv", **changes) -> Dataset:
    content = (ROOT / "data/sample" / source).read_bytes()
    path = tmp_path / source
    path.write_bytes(content)
    values = dict(
        name="fixture",
        path=path,
        format="csv",
        id_column="id",
        required_columns=["name"],
        source_description="Synthetic",
        source_crs="EPSG:4326",
        license="Fixture",
        max_invalid_fraction=0.5,
    )
    values.update(changes)
    return Dataset(**values)


def test_csv_end_to_end_provenance_reconciliation_and_retry(tmp_path):
    config = dataset(tmp_path, target_crs="EPSG:26917")
    original = config.path.read_bytes()
    report_path = run_dataset(config, tmp_path / "out")
    report = json.loads(report_path.read_text())
    assert report["status"] == "succeeded"
    assert report["total"] == report["accepted"] + report["rejected"] == 6
    assert report["accepted"] == report["rejected"] == 3
    assert report["source_sha256"] == hashlib.sha256(original).hexdigest()
    assert (report_path.parent / "source.csv").read_bytes() == original
    rejected = [
        json.loads(line)
        for line in (report_path.parent / "quarantine.jsonl").read_text().splitlines()
    ]
    assert [row["row_id"] for row in rejected] == [3, 4, 6]
    assert rejected[1]["record"]["longitude"] == "invalid"
    accepted = gpd.read_file(report_path.parent / "accepted.geojson")
    assert set(accepted["id"]) == {"A", "B", "E"}
    assert accepted.crs.to_epsg() == 4326
    assert accepted.geometry.iloc[0].x == pytest.approx(-80.5)
    before = (report_path.parent / "accepted.geojson").read_bytes()
    assert run_dataset(config, tmp_path / "out") == report_path
    assert (report_path.parent / "accepted.geojson").read_bytes() == before
    assert config.path.read_bytes() == original
    assert len(list((tmp_path / "out/fixture").iterdir())) == 1


def test_geojson_end_to_end(tmp_path):
    config = dataset(tmp_path, "points.geojson", format="geojson")
    report = json.loads(run_dataset(config, tmp_path / "out").read_text())
    assert report["accepted"] == report["rejected"] == 1


def test_quality_gate_persists_evidence_and_never_calls_database(tmp_path, monkeypatch):
    config = dataset(tmp_path, max_invalid_fraction=0, sink="postgis")
    called = []
    monkeypatch.setattr(
        "geoflow.loading.postgis.load_postgis", lambda *a, **kw: called.append(True)
    )
    with pytest.raises(QualityError):
        run_dataset(config, tmp_path / "out")
    report_path = next((tmp_path / "out").rglob("report.json"))
    report = json.loads(report_path.read_text())
    assert report["status"] == "quality_failed"
    assert report["database_committed"] is False
    assert (report_path.parent / "quarantine.jsonl").exists()
    assert called == []
    assert not (report_path.parent / ".lock").exists()


def test_malformed_document_retains_original_and_failed_report(tmp_path):
    config = dataset(tmp_path, "points.geojson", format="geojson")
    config.path.write_bytes(b"{broken")
    with pytest.raises(IngestionError):
        run_dataset(config, tmp_path / "out")
    report_path = next((tmp_path / "out").rglob("report.json"))
    assert json.loads(report_path.read_text())["status"] == "failed"
    assert (report_path.parent / "source.geojson").read_bytes() == b"{broken"


def test_lock_prevents_overlapping_run(tmp_path):
    config = dataset(tmp_path)
    report = run_dataset(config, tmp_path / "out")
    lock = report.parent / ".lock"
    lock.write_text("test")
    with pytest.raises(RunLockedError):
        run_dataset(config, tmp_path / "out")
    assert lock.exists()


def test_cli_workflow_and_errors(tmp_path):
    config = dataset(tmp_path)
    yaml = tmp_path / "config.yml"
    yaml.write_text("""datasets:
  - name: fixture
    path: points.csv
    format: csv
    id_column: id
    source_crs: EPSG:4326
    source_description: Synthetic
    license: Test
    max_invalid_fraction: 0.5
output_dir: out
""")
    runner = CliRunner()
    assert runner.invoke(app, ["validate-config", str(yaml)]).exit_code == 0
    result = runner.invoke(app, ["run", str(yaml), "--dataset", "fixture"])
    assert result.exit_code == 0, result.output
    report_path = next((tmp_path / "out").rglob("report.json"))
    result = runner.invoke(app, ["report", str(report_path)])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["status"] == "succeeded"
    assert runner.invoke(app, ["run", str(yaml), "--dataset", "missing"]).exit_code == 1
    assert runner.invoke(app, ["report", str(config.path)]).exit_code == 2


def test_database_failure_keeps_report_and_does_not_leak_url(tmp_path, monkeypatch, caplog):
    from geoflow.exceptions import GeoFlowError

    config = dataset(tmp_path, sink="postgis")
    monkeypatch.setenv("GEOFLOW_DATABASE_URL", "postgresql://user:secret@localhost/db")

    def fail(*args, **kwargs):
        raise RuntimeError("postgresql://user:secret@localhost/db")

    monkeypatch.setattr("geoflow.database.repository.publish_run", fail)
    with pytest.raises(GeoFlowError, match="Pipeline failed"):
        run_dataset(config, tmp_path / "out")
    report = next((tmp_path / "out").rglob("report.json")).read_text()
    assert "secret" not in report + caplog.text
    assert json.loads(report)["database_committed"] is False


def test_duplicate_json_members_fail_instead_of_overwriting(tmp_path):
    config = dataset(tmp_path, "points.geojson", format="geojson")
    config.path.write_text('{"type":"FeatureCollection","features":[],"features":[]}')
    with pytest.raises(IngestionError):
        run_dataset(config, tmp_path / "out")
    report = next((tmp_path / "out").rglob("report.json"))
    assert json.loads(report.read_text())["status"] == "failed"


def test_fully_rejected_input_never_clears_sink(tmp_path, monkeypatch):
    config = dataset(tmp_path, sink="postgis", max_invalid_fraction=1)
    config.path.write_text("id,name,longitude,latitude\nA,Bad,no,43\n")
    called = []
    monkeypatch.setattr(
        "geoflow.loading.postgis.load_postgis", lambda *a, **kw: called.append(True)
    )
    with pytest.raises(QualityError, match="No valid records"):
        run_dataset(config, tmp_path / "out")
    report = json.loads(next((tmp_path / "out").rglob("report.json")).read_text())
    assert report["accepted"] == 0 and report["rejected"] == 1
    assert called == []
