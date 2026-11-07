"""SQLAlchemy schema for immutable snapshots and atomic run publication."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import UserDefinedType


class Geometry(UserDefinedType):
    cache_ok = True

    def get_col_spec(self, **kw):
        return "geometry(Geometry,26917)"


class Base(DeclarativeBase):
    pass


class DatasetRecord(Base):
    __tablename__ = "datasets"
    __table_args__ = {"schema": "geoflow"}
    name: Mapped[str] = mapped_column(String(48), primary_key=True)
    configuration: Mapped[dict[str, Any]] = mapped_column(JSONB)
    source_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SourceSnapshot(Base):
    __tablename__ = "source_snapshots"
    __table_args__ = (
        UniqueConstraint("dataset_name", "sha256"),
        UniqueConstraint("dataset_name", "id"),
        CheckConstraint("octet_length(raw_bytes) <= 100000000"),
        {"schema": "geoflow"},
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    dataset_name: Mapped[str] = mapped_column(ForeignKey("geoflow.datasets.name"))
    sha256: Mapped[str] = mapped_column(String(64))
    raw_bytes: Mapped[bytes] = mapped_column(LargeBinary)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["dataset_name", "snapshot_id"],
            ["geoflow.source_snapshots.dataset_name", "geoflow.source_snapshots.id"],
        ),
        CheckConstraint("status IN ('succeeded','quality_failed','failed')"),
        {"schema": "geoflow"},
    )
    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    dataset_name: Mapped[str] = mapped_column(ForeignKey("geoflow.datasets.name"))
    snapshot_id: Mapped[str] = mapped_column(String(64))
    configuration: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20))
    error_type: Mapped[str | None] = mapped_column(String(128))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ValidatedFeature(Base):
    __tablename__ = "validated_features"
    __table_args__ = (
        UniqueConstraint("run_id", "feature_id"),
        CheckConstraint("ST_IsValid(geometry) AND NOT ST_IsEmpty(geometry)"),
        CheckConstraint(
            "GeometryType(geometry) IN ('POINT','MULTIPOINT','LINESTRING',"
            "'MULTILINESTRING','POLYGON','MULTIPOLYGON')"
        ),
        Index("validated_features_geometry_gist", "geometry", postgresql_using="gist"),
        {"schema": "geoflow"},
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey("geoflow.pipeline_runs.run_id", ondelete="CASCADE"), primary_key=True
    )
    row_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    feature_id: Mapped[str] = mapped_column(String(36))
    source_identifier: Mapped[Any] = mapped_column(JSONB, nullable=False)
    properties: Mapped[dict[str, Any]] = mapped_column(JSONB)
    geometry: Mapped[Any] = mapped_column(Geometry(), nullable=False)


class QuarantinedFeature(Base):
    __tablename__ = "quarantined_features"
    __table_args__ = {"schema": "geoflow"}
    run_id: Mapped[str] = mapped_column(
        ForeignKey("geoflow.pipeline_runs.run_id", ondelete="CASCADE"), primary_key=True
    )
    row_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    raw_record: Mapped[dict[str, Any]] = mapped_column(JSONB)
    error_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB)


class ValidationIssueRecord(Base):
    __tablename__ = "validation_issues"
    __table_args__ = (
        CheckConstraint("severity IN ('INFO','WARNING','ERROR','FATAL')"),
        Index("validation_issues_run_code_idx", "run_id", "code"),
        {"schema": "geoflow"},
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey("geoflow.pipeline_runs.run_id", ondelete="CASCADE"), primary_key=True
    )
    issue_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    row_id: Mapped[int] = mapped_column(BigInteger)
    code: Mapped[str] = mapped_column(String(128))
    severity: Mapped[str] = mapped_column(String(10))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB)


class PipelineMetric(Base):
    __tablename__ = "pipeline_metrics"
    __table_args__ = (
        CheckConstraint("input_records = accepted_records + quarantined_records"),
        CheckConstraint(
            "(input_records IS NULL AND accepted_records IS NULL AND "
            "quarantined_records IS NULL) OR (input_records IS NOT NULL AND "
            "accepted_records IS NOT NULL AND quarantined_records IS NOT NULL)"
        ),
        CheckConstraint("accepted_records IS NULL OR published_records <= accepted_records"),
        CheckConstraint(
            "input_records >= 0 AND accepted_records >= 0 AND quarantined_records >= 0"
        ),
        CheckConstraint("published_records >= 0 AND duration_seconds >= 0"),
        {"schema": "geoflow"},
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey("geoflow.pipeline_runs.run_id", ondelete="CASCADE"), primary_key=True
    )
    input_records: Mapped[int | None] = mapped_column(BigInteger)
    accepted_records: Mapped[int | None] = mapped_column(BigInteger)
    quarantined_records: Mapped[int | None] = mapped_column(BigInteger)
    published_records: Mapped[int] = mapped_column(BigInteger)
    duration_seconds: Mapped[float] = mapped_column(Float)
    quality_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
