"""Atomic run publication and recoverable failed-run audit records."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime
from typing import Any, Literal

import numpy as np
from shapely import get_coordinates
from sqlalchemy import create_engine, delete, func, insert, select, text
from sqlalchemy.exc import SQLAlchemyError

from geoflow.config import Dataset
from geoflow.database.models import (
    DatasetRecord,
    PipelineMetric,
    PipelineRun,
    QuarantinedFeature,
    SourceSnapshot,
    ValidatedFeature,
    ValidationIssueRecord,
)
from geoflow.validation.models import ValidationResult


class StorageError(RuntimeError):
    """Safe storage failure without driver credentials or record contents."""


def normalize_url(url: str) -> str:
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    if not url.startswith("postgresql+psycopg://"):
        raise StorageError("Set a PostgreSQL psycopg connection URL in the environment")
    return url


def _configuration(config: Dataset) -> dict[str, Any]:
    values = config.model_dump(mode="json")
    values.pop("path", None)
    return values


def publish_run(
    *,
    database_url: str,
    config: Dataset,
    result: ValidationResult | None,
    run_id: str,
    source_bytes: bytes,
    provenance: dict[str, Any],
    started_at: str | datetime,
    duration_seconds: float,
    status: Literal["succeeded", "quality_failed", "failed"] = "succeeded",
    error_type: str | None = None,
) -> int:
    """Commit history and all child rows together; identical successful runs are no-ops.

    Failed runs retain source/quality evidence but publish zero accepted features.
    An identical failed run may be retried; a successful run is immutable. No
    transaction can publish only some of its children. Geometry storage is 2D
    EPSG:26917; original source bytes remain byte-for-byte recoverable.
    """
    engine = None
    try:
        if status not in {"succeeded", "quality_failed", "failed"}:
            raise StorageError("Unsupported run status")
        if not run_id or len(run_id) > 128 or len(source_bytes) > config.max_source_bytes:
            raise StorageError("Run identity or source size is invalid")
        if not math.isfinite(duration_seconds) or duration_seconds < 0:
            raise StorageError("Duration must be finite and nonnegative")
        began = datetime.fromisoformat(started_at) if isinstance(started_at, str) else started_at
        if began.tzinfo is None:
            raise StorageError("Run timestamps must include a time zone")
        if status != "failed" and result is None:
            raise StorageError("Validated dispositions are required for this status")
        if result is not None:
            summary = result.summary
            accepted_ids = list(result.accepted.get("__row_id", []))
            rejected_ids = [row.row_id for row in result.quarantined]
            ids = accepted_ids + rejected_ids
            if (
                len(ids) != len(set(ids))
                or len(ids) != summary.input_records
                or len(accepted_ids) != summary.accepted_records
                or len(rejected_ids) != summary.quarantined_records
                or summary.input_records != summary.accepted_records + summary.quarantined_records
                or any(issue.row_id not in set(ids) for issue in result.issues)
                or len(result.issues) != summary.issue_count
            ):
                raise StorageError("Validation dispositions do not reconcile")
            if status == "succeeded" and (
                summary.has_fatal
                or not summary.accepted_records
                or summary.quarantined_records / summary.input_records > config.max_invalid_fraction
            ):
                raise StorageError("A failed quality gate cannot be published successfully")
        checksum = hashlib.sha256(source_bytes).hexdigest()
        snapshot_id = hashlib.sha256((config.name + ":" + checksum).encode()).hexdigest()
        configuration = _configuration(config)
        rows = []
        if status == "succeeded":
            assert result is not None
            if result.accepted.crs is None:
                raise StorageError("Accepted geometry has no CRS")
            frame = result.accepted.to_crs(26917)
            properties = json.loads(
                frame.drop(columns=frame.geometry.name).to_json(orient="records")
            )
            for values, geometry in zip(properties, frame.geometry, strict=True):
                if (
                    geometry is None
                    or geometry.is_empty
                    or not geometry.is_valid
                    or not np.isfinite(get_coordinates(geometry, include_z=geometry.has_z)).all()
                ):
                    raise StorageError("Storage transformation produced invalid geometry")
                rows.append(
                    dict(
                        run_id=run_id,
                        row_id=values["__row_id"],
                        feature_id=values["__feature_id"],
                        source_identifier=json.dumps(values["__source_id"]),
                        properties=json.dumps(values, allow_nan=False),
                        wkb=geometry.wkb,
                    )
                )
        engine = create_engine(normalize_url(database_url), pool_pre_ping=True)
        with engine.begin() as conn:
            conn.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                {"key": "geoflow.history." + config.name},
            )
            existing = (
                conn.execute(select(PipelineRun.__table__).where(PipelineRun.run_id == run_id))
                .mappings()
                .first()
            )
            if existing:
                if (
                    existing["dataset_name"] != config.name
                    or existing["snapshot_id"] != snapshot_id
                    or existing["configuration"] != configuration
                ):
                    raise StorageError("Run ID conflicts with different source or configuration")
                if existing["status"] == "succeeded":
                    published = conn.scalar(
                        select(PipelineMetric.published_records).where(
                            PipelineMetric.run_id == run_id
                        )
                    )
                    if published is None:
                        raise StorageError("Successful run has no publication metrics")
                    return int(published)
                conn.execute(delete(PipelineRun).where(PipelineRun.run_id == run_id))
            if not conn.scalar(select(DatasetRecord.name).where(DatasetRecord.name == config.name)):
                conn.execute(
                    insert(DatasetRecord).values(
                        name=config.name,
                        configuration=configuration,
                        source_metadata={
                            "description": config.source_description,
                            "license": config.license,
                        },
                    )
                )
            if not conn.scalar(select(SourceSnapshot.id).where(SourceSnapshot.id == snapshot_id)):
                conn.execute(
                    insert(SourceSnapshot).values(
                        id=snapshot_id,
                        dataset_name=config.name,
                        sha256=checksum,
                        raw_bytes=source_bytes,
                        provenance=provenance,
                    )
                )
            conn.execute(
                insert(PipelineRun).values(
                    run_id=run_id,
                    dataset_name=config.name,
                    snapshot_id=snapshot_id,
                    configuration=configuration,
                    status=status,
                    error_type=error_type,
                    started_at=began,
                    finished_at=datetime.now(UTC),
                )
            )
            if rows:
                conn.execute(
                    text("""INSERT INTO geoflow.validated_features
                    (run_id,row_id,feature_id,source_identifier,properties,geometry)
                    VALUES (:run_id,:row_id,:feature_id,CAST(:source_identifier AS jsonb),
                    CAST(:properties AS jsonb),ST_Force2D(ST_GeomFromWKB(:wkb,26917)))"""),
                    rows,
                )
            if result is not None:
                quarantine = [
                    dict(
                        run_id=run_id,
                        row_id=r.row_id,
                        raw_record=r.record,
                        error_metadata={
                            "reasons": r.reasons,
                            "issues": [i.model_dump(mode="json") for i in r.issues],
                        },
                    )
                    for r in result.quarantined
                ]
                if quarantine:
                    conn.execute(insert(QuarantinedFeature), quarantine)
                issues = [
                    dict(
                        run_id=run_id,
                        issue_number=n,
                        row_id=i.row_id,
                        code=i.code,
                        severity=i.severity,
                        details=i.model_dump(mode="json"),
                    )
                    for n, i in enumerate(result.issues, 1)
                ]
                if issues:
                    conn.execute(insert(ValidationIssueRecord), issues)
                summary_values = result.summary.model_dump(mode="json")
            else:
                summary_values = None
            conn.execute(
                insert(PipelineMetric).values(
                    run_id=run_id,
                    input_records=summary_values["input_records"] if summary_values else None,
                    accepted_records=summary_values["accepted_records"] if summary_values else None,
                    quarantined_records=summary_values["quarantined_records"]
                    if summary_values
                    else None,
                    published_records=len(rows),
                    duration_seconds=duration_seconds,
                    quality_summary=summary_values,
                )
            )
            actual = conn.scalar(
                select(func.count())
                .select_from(ValidatedFeature)
                .where(ValidatedFeature.run_id == run_id)
            )
            rejected = conn.scalar(
                select(func.count())
                .select_from(QuarantinedFeature)
                .where(QuarantinedFeature.run_id == run_id)
            )
            if actual != len(rows) or (result is not None and rejected != len(result.quarantined)):
                raise StorageError("Stored row counts do not reconcile")
        return len(rows)
    except StorageError:
        raise
    except (SQLAlchemyError, ValueError, TypeError, KeyError, OverflowError):
        raise StorageError(
            "Run storage failed; transaction rolled back. "
            "Check migrations and database availability."
        ) from None
    finally:
        if engine is not None:
            engine.dispose()
