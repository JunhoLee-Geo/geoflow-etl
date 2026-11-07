"""Real PostGIS tests; only use a disposable database migrated by Alembic."""

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from geoflow.config import load_config
from geoflow.database.repository import StorageError, publish_run
from geoflow.ingestion.readers import ingest
from geoflow.validation.quality import validate

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def storage():
    url = os.getenv("GEOFLOW_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Requires disposable PostGIS with Alembic migrations")
    engine = create_engine(url.replace("postgresql://", "postgresql+psycopg://", 1))
    cfg = load_config(ROOT / "configs/quality.yml").datasets[0]
    cfg.name = "test_" + uuid4().hex
    source = cfg.path.read_bytes()
    result = validate(ingest(source, cfg), cfg)
    args = dict(
        database_url=url,
        config=cfg,
        result=result,
        run_id=uuid4().hex,
        source_bytes=source,
        provenance={"source_name": "synthetic"},
        started_at=datetime.now(UTC),
        duration_seconds=1.25,
    )
    try:
        yield engine, args
    finally:
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM geoflow.pipeline_runs WHERE dataset_name=:name"),
                {"name": cfg.name},
            )
            conn.execute(
                text("DELETE FROM geoflow.source_snapshots WHERE dataset_name=:name"),
                {"name": cfg.name},
            )
            conn.execute(text("DELETE FROM geoflow.datasets WHERE name=:name"), {"name": cfg.name})
        engine.dispose()


def count(conn, table, run_id):
    return conn.scalar(
        text(f"SELECT count(*) FROM geoflow.{table} WHERE run_id=:id"), {"id": run_id}
    )


def test_atomic_history_reconciliation_and_identical_retry(storage):
    engine, args = storage
    assert publish_run(**args) == 1
    assert publish_run(**args) == 1
    with engine.connect() as conn:
        assert count(conn, "pipeline_runs", args["run_id"]) == 1
        assert count(conn, "validated_features", args["run_id"]) == 1
        assert count(conn, "quarantined_features", args["run_id"]) == 9
        assert count(conn, "validation_issues", args["run_id"]) == 14
        assert conn.scalar(
            text(
                "SELECT bool_and(ST_SRID(geometry)=26917 AND ST_IsValid(geometry)) "
                "FROM geoflow.validated_features WHERE run_id=:id"
            ),
            {"id": args["run_id"]},
        )
        assert (
            conn.scalar(
                text(
                    "SELECT count(*) FROM pg_indexes WHERE schemaname='geoflow' "
                    "AND tablename='validated_features' AND indexdef LIKE '%USING gist%'"
                )
            )
            >= 1
        )
        snapshot = conn.execute(
            text("SELECT raw_bytes, sha256 FROM geoflow.source_snapshots WHERE dataset_name=:name"),
            {"name": args["config"].name},
        ).one()
        assert bytes(snapshot.raw_bytes) == args["source_bytes"]
        assert snapshot.sha256 == hashlib.sha256(args["source_bytes"]).hexdigest()


def test_new_snapshot_preserves_prior_history(storage):
    engine, args = storage
    publish_run(**args)
    newer = {**args, "run_id": uuid4().hex, "source_bytes": args["source_bytes"] + b"\n"}
    publish_run(**newer)
    with engine.connect() as conn:
        assert (
            conn.scalar(
                text("SELECT count(*) FROM geoflow.source_snapshots WHERE dataset_name=:name"),
                {"name": args["config"].name},
            )
            == 2
        )
        assert count(conn, "validated_features", args["run_id"]) == 1
        assert count(conn, "validated_features", newer["run_id"]) == 1


def test_failed_quality_run_has_evidence_but_no_published_features(storage):
    engine, args = storage
    publish_run(**args, status="quality_failed", error_type="QualityError")
    with engine.connect() as conn:
        assert count(conn, "validated_features", args["run_id"]) == 0
        assert count(conn, "quarantined_features", args["run_id"]) == 9
        assert count(conn, "validation_issues", args["run_id"]) == 14
    publish_run(**args)
    with engine.connect() as conn:
        assert count(conn, "validated_features", args["run_id"]) == 1


def test_mid_transaction_failure_rolls_back_all_new_records(storage):
    engine, args = storage
    constraint = "test_fail_" + uuid4().hex
    with engine.begin() as conn:
        conn.execute(
            text(
                f"ALTER TABLE geoflow.pipeline_metrics ADD CONSTRAINT {constraint} "
                f"CHECK (run_id <> '{args['run_id']}')"
            )
        )
    try:
        with pytest.raises(StorageError, match="rolled back"):
            publish_run(**args)
        with engine.connect() as conn:
            assert count(conn, "pipeline_runs", args["run_id"]) == 0
            assert count(conn, "validated_features", args["run_id"]) == 0
            assert (
                conn.scalar(
                    text("SELECT count(*) FROM geoflow.source_snapshots WHERE dataset_name=:name"),
                    {"name": args["config"].name},
                )
                == 0
            )
    finally:
        with engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE geoflow.pipeline_metrics DROP CONSTRAINT {constraint}"))


def test_run_identity_collision_cannot_replace_success(storage):
    engine, args = storage
    publish_run(**args)
    with pytest.raises(StorageError):
        publish_run(**{**args, "source_bytes": b"different"})
    with engine.connect() as conn:
        assert count(conn, "validated_features", args["run_id"]) == 1


def test_mixed_geometry_and_typed_source_identifiers(storage):
    engine, args = storage
    cfg = args["config"]
    cfg.geometry_types = ["Point", "LineString"]
    cfg.quality_rules = []
    source = json.dumps(
        {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"id": "001", "name": "synthetic"},
                    "geometry": {"type": "Point", "coordinates": [-80.5, 43.4]},
                },
                {
                    "type": "Feature",
                    "properties": {"id": 2, "name": "synthetic"},
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[-80.5, 43.4], [-80.4, 43.5]],
                    },
                },
            ],
        }
    ).encode()
    result = validate(ingest(source, cfg), cfg)
    assert publish_run(**{**args, "source_bytes": source, "result": result}) == 2
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT source_identifier, GeometryType(geometry) AS kind "
                "FROM geoflow.validated_features WHERE run_id=:id ORDER BY row_id"
            ),
            {"id": args["run_id"]},
        ).all()
        assert rows == [("001", "POINT"), (2, "LINESTRING")]


def test_prevalidation_failure_has_null_counts_and_raw_snapshot(storage):
    engine, args = storage
    publish_run(**{**args, "result": None, "status": "failed", "error_type": "IngestionError"})
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT input_records, accepted_records, quarantined_records, published_records "
                "FROM geoflow.pipeline_metrics WHERE run_id=:id"
            ),
            {"id": args["run_id"]},
        ).one()
        assert tuple(row) == (None, None, None, 0)


def test_pipeline_end_to_end_database_publication(storage, tmp_path, monkeypatch):
    from geoflow.orchestration.pipeline import run_dataset

    engine, args = storage
    cfg = args["config"]
    cfg.sink = "postgis"
    monkeypatch.setenv("GEOFLOW_DATABASE_URL", args["database_url"])
    path = run_dataset(cfg, tmp_path)
    report = json.loads(path.read_text())
    assert report["database_committed"] is True
    assert report["loaded"] == 1
    assert run_dataset(cfg, tmp_path) == path
    with engine.connect() as conn:
        assert count(conn, "validated_features", report["run_id"]) == 1
