"""Monitoring SQL exercised against a real migrated disposable PostGIS database."""

import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from geoflow.api.app import create_app
from geoflow.api.store import MonitoringStore
from geoflow.config import load_config
from geoflow.database.repository import normalize_url, publish_run
from geoflow.ingestion.readers import ingest
from geoflow.validation.quality import validate

pytestmark = pytest.mark.integration


@pytest.fixture
def published():
    url = os.environ.get("GEOFLOW_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Requires disposable migrated PostGIS")
    engine = create_engine(normalize_url(url))
    cfg = load_config(Path(__file__).resolve().parents[2] / "configs/quality.yml").datasets[0]
    cfg.name = "api_" + uuid4().hex
    raw = cfg.path.read_bytes()
    run_id = uuid4().hex
    publish_run(
        database_url=url,
        config=cfg,
        result=validate(ingest(raw, cfg), cfg),
        run_id=run_id,
        source_bytes=raw,
        provenance={"private": "PRIVATE"},
        started_at=datetime.now(UTC),
        duration_seconds=0.5,
    )
    try:
        yield MonitoringStore(engine), cfg.name, run_id
    finally:
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM geoflow.pipeline_runs WHERE dataset_name=:id"), {"id": cfg.name}
            )
            conn.execute(
                text("DELETE FROM geoflow.source_snapshots WHERE dataset_name=:id"),
                {"id": cfg.name},
            )
            conn.execute(text("DELETE FROM geoflow.datasets WHERE name=:id"), {"id": cfg.name})
        engine.dispose()


def test_monitoring_read_endpoints_real_database(published):
    store, dataset_id, run_id = published
    with TestClient(create_app(store, preview_datasets={dataset_id})) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/overview").json()["data"]["total_runs"] >= 1
        runs = client.get("/runs", params={"dataset_id": dataset_id, "limit": 1}).json()
        assert runs["pagination"]["total"] == 1
        assert runs["data"][0]["run_id"] == run_id
        quality = client.get(f"/runs/{run_id}/quality").json()["data"]
        assert quality["accepted_records"] == 1
        assert quality["quarantined_records"] == 9
        assert quality["pass_rate"] == 0.1
        assert quality["severity_counts"]["ERROR"] == 14
        issues = client.get(f"/runs/{run_id}/issues?limit=2&offset=2").json()
        assert issues["pagination"]["total"] == 14
        assert len(issues["data"]) == 2
        summary = client.get(f"/datasets/{dataset_id}/summary").json()["data"]
        assert summary["total_runs"] == 1
        preview = client.get(f"/datasets/{dataset_id}/preview").json()["data"]
        assert preview["returned_features"] == 1
        assert preview["geojson"]["features"][0]["geometry"]["coordinates"] == [-80.5, 43.4]
        assert "PRIVATE" not in str(summary) + str(issues) + str(preview)


def test_database_read_only_transaction_cannot_mutate(published):
    store, _, _ = published
    with pytest.raises(SQLAlchemyError):
        with store.connection() as conn:
            conn.execute(text("DELETE FROM geoflow.datasets WHERE false"))


def test_real_empty_filtered_page_and_missing_run(published):
    store, _, _ = published
    with TestClient(create_app(store)) as client:
        response = client.get("/runs?dataset_id=does_not_exist").json()
        assert response["data"] == []
        assert response["pagination"]["total"] == 0
        assert client.get("/runs/does_not_exist").status_code == 404
