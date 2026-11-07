"""Explicit public fields; raw records, identifiers and source URLs are excluded."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class Envelope[T](BaseModel):
    data: T


class Pagination(BaseModel):
    limit: int
    offset: int
    total: int


class Page[T](BaseModel):
    data: list[T]
    pagination: Pagination


class Health(BaseModel):
    status: Literal["ready"] = "ready"
    mode: Literal["read_only"] = "read_only"


class DatasetView(BaseModel):
    dataset_id: str
    created_at: datetime
    source_crs: str | None
    target_crs: str | None
    geometry_types: list[str]
    source_format: Literal["csv", "geojson", "gpkg"] | None


class RunView(BaseModel):
    run_id: str
    dataset_id: str
    status: Literal["succeeded", "quality_failed", "failed"]
    started_at: datetime
    finished_at: datetime
    duration_seconds: float
    input_records: int | None
    accepted_records: int | None
    quarantined_records: int | None
    published_records: int


class Overview(BaseModel):
    total_runs: int
    successful_runs: int
    failed_runs: int
    input_records: int
    accepted_records: int
    quarantined_records: int
    published_records: int
    unmeasured_runs: int


class QualityView(BaseModel):
    run_id: str
    pass_rate: float | None = Field(ge=0, le=1)
    input_records: int | None
    accepted_records: int | None
    quarantined_records: int | None
    rule_counts: dict[str, int]
    severity_counts: dict[str, int]


class IssueView(BaseModel):
    issue_number: int
    code: str
    severity: Literal["INFO", "WARNING", "ERROR", "FATAL"]


class DatasetSummary(DatasetView):
    total_runs: int
    successful_runs: int
    failed_runs: int
    latest_run_id: str | None


class PreviewGeometry(BaseModel):
    type: Literal["Point", "MultiPoint", "LineString", "MultiLineString", "Polygon", "MultiPolygon"]
    coordinates: list


class PreviewFeature(BaseModel):
    type: Literal["Feature"] = "Feature"
    geometry: PreviewGeometry
    properties: dict[str, str] = Field(default_factory=dict, max_length=0)


class FeatureCollection(BaseModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[PreviewFeature] = Field(max_length=100)


class Preview(BaseModel):
    dataset_id: str
    run_id: str | None
    geojson: FeatureCollection
    sampled_features: int
    returned_features: int
    omitted_features: int
    limit: int
    storage_crs: Literal["EPSG:26917"] = "EPSG:26917"
    preview_crs: Literal["EPSG:4326"] = "EPSG:4326"
    simplification_tolerance_m: int = 25
