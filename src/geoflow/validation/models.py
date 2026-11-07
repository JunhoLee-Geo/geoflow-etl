"""Typed configuration and machine-readable validation outcomes."""

import math
from dataclasses import dataclass
from typing import Any, Literal

import geopandas as gpd
from pydantic import BaseModel, ConfigDict, Field, model_validator

Severity = Literal["INFO", "WARNING", "ERROR", "FATAL"]
Action = Literal["report", "quarantine"]


class ValidationRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    kind: Literal["required", "type", "not_null", "duplicate_record", "bounds", "range", "enum"]
    field: str | None = None
    severity: Severity = "ERROR"
    action: Action = "quarantine"
    parameters: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_parameters(self) -> "ValidationRule":
        field_kinds = {"required", "type", "not_null", "range", "enum"}
        if self.kind in field_kinds:
            if not self.field or self.field.startswith("__") or self.field == "geometry":
                raise ValueError("A non-reserved field is required")
        elif self.field is not None:
            raise ValueError("This rule does not accept a field")
        p = self.parameters
        allowed = {
            "required": set(),
            "not_null": set(),
            "type": {"dtype"},
            "bounds": {"min_x", "min_y", "max_x", "max_y"},
            "range": {"min", "max"},
            "enum": {"values"},
            "duplicate_record": {"fields"},
        }[self.kind]
        if not set(p) <= allowed:
            raise ValueError("Unsupported rule parameters")
        if self.kind == "type" and p.get("dtype") not in (
            "string",
            "integer",
            "number",
            "boolean",
            "object",
            "array",
        ):
            raise ValueError("type requires a supported dtype")
        if self.kind in {"bounds", "range"}:
            if not p or (self.kind == "bounds" and set(p) != allowed):
                raise ValueError("Spatial bounds require four limits; range needs a limit")
            if any(
                isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                for v in p.values()
            ):
                raise ValueError("Limits must be finite numbers")
            pairs = (
                [("min_x", "max_x"), ("min_y", "max_y")]
                if self.kind == "bounds"
                else [("min", "max")]
            )
            if any(a in p and b in p and p[a] > p[b] for a, b in pairs):
                raise ValueError("Minimum must not exceed maximum")
        if self.kind == "enum":
            values = p.get("values")
            if (
                not isinstance(values, list)
                or not values
                or any(
                    not isinstance(v, (str, int, float, bool, type(None)))
                    or isinstance(v, float)
                    and not math.isfinite(v)
                    for v in values
                )
            ):
                raise ValueError("enum requires a nonempty list of finite JSON scalars")
        if self.kind == "duplicate_record" and "fields" in p:
            fields = p["fields"]
            if (
                not isinstance(fields, list)
                or not fields
                or any(
                    not isinstance(v, str) or not v or v.startswith("__") or v == "geometry"
                    for v in fields
                )
                or len(fields) != len(set(fields))
            ):
                raise ValueError("duplicate fields must be unique non-reserved field names")
        return self


class ValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    row_id: int
    code: str
    severity: Severity
    message: str
    field: str | None = None
    action: Action = "quarantine"
    reason: str


class QuarantinedRecord(BaseModel):
    row_id: int
    record: dict[str, Any]
    issues: list[ValidationIssue]
    reasons: list[str]


class DatasetQualitySummary(BaseModel):
    input_records: int
    accepted_records: int
    quarantined_records: int
    issue_free_records: int
    issue_count: int
    pass_rate: float
    rule_counts: dict[str, int]
    has_fatal: bool

    @model_validator(mode="after")
    def reconcile(self) -> "DatasetQualitySummary":
        if self.input_records != self.accepted_records + self.quarantined_records:
            raise ValueError("Every input record must have exactly one final disposition")
        return self


@dataclass
class ValidationResult:
    accepted: gpd.GeoDataFrame
    quarantined: list[QuarantinedRecord]
    issues: list[ValidationIssue]
    summary: DatasetQualitySummary

    @property
    def rejected(self) -> list[dict[str, Any]]:
        return [row.model_dump(mode="json") for row in self.quarantined]

    @property
    def counts(self) -> dict[str, int]:
        return self.summary.rule_counts
