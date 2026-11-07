import json
import signal
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from geoflow.cli import app
from geoflow.config import load_config
from geoflow.exceptions import PipelineInterrupted
from geoflow.orchestration.pipeline import run_dataset

ROOT = Path(__file__).resolve().parents[2]


def test_interrupted_validation_retains_source_report_and_releases_lock(tmp_path, monkeypatch):
    cfg = load_config(ROOT / "configs/sample.yml").datasets[0]

    def interrupt(*args):
        raise PipelineInterrupted("interrupted")

    monkeypatch.setattr("geoflow.orchestration.pipeline.validate", interrupt)
    with pytest.raises(PipelineInterrupted):
        run_dataset(cfg, tmp_path)
    report = next(tmp_path.rglob("report.json"))
    assert json.loads(report.read_text())["status"] == "failed"
    assert (report.parent / "source.csv").read_bytes() == cfg.path.read_bytes()
    assert not (report.parent / ".lock").exists()


def test_cli_signal_has_explicit_exit_code_and_restores_handlers(monkeypatch):
    previous = signal.getsignal(signal.SIGTERM)

    def terminate(*args):
        signal.raise_signal(signal.SIGTERM)

    monkeypatch.setattr("geoflow.cli.run_dataset", terminate)
    result = CliRunner().invoke(
        app, ["run", str(ROOT / "configs/sample.yml"), "--dataset", "sample_points"]
    )
    assert result.exit_code == 130
    assert signal.getsignal(signal.SIGTERM) == previous


def test_workflow_schedules_disabled_and_artifacts_exclude_raw_records():
    workflows = ROOT / ".github/workflows"
    for path in workflows.glob("*.yml"):
        doc = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
        assert "schedule" not in doc["on"]
        assert doc["permissions"] == {"contents": "read"}
    manual = yaml.load((workflows / "manual-run.yml").read_text(), Loader=yaml.BaseLoader)
    artifact = manual["jobs"]["execute"]["steps"][-1]["with"]
    assert artifact["retention-days"] == "7"
    assert artifact["path"].splitlines() == [
        "reports/**/report.json",
        "reports/**/quality.json",
        "reports/**/issues.csv",
    ]


def test_compose_public_ports_are_loopback_and_dashboard_has_no_db_secret():
    doc = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    for service in doc["services"].values():
        assert all(port.startswith("127.0.0.1:") for port in service.get("ports", []))
    assert "POSTGRES_PASSWORD" not in doc["services"]["api"]["environment"]
    assert set(doc["services"]["dashboard"]["environment"]) == {"GEOFLOW_API_URL"}
    assert doc["services"]["etl"]["profiles"] == ["etl"]
