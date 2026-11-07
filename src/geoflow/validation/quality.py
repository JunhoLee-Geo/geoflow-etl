"""Deterministic rule evaluation with one disposition and many issues per input."""

import hashlib
import json
import math
from collections import Counter
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import geopandas as gpd
import numpy as np
from pyproj import CRS, Transformer
from shapely import get_coordinates
from shapely.validation import explain_validity

from geoflow.config import Dataset
from geoflow.ingestion.readers import Record
from geoflow.validation.models import (
    DatasetQualitySummary,
    QuarantinedRecord,
    ValidationIssue,
    ValidationResult,
    ValidationRule,
)


def _null(value: Any) -> bool:
    return value is None or isinstance(value, float) and math.isnan(value)


def _fingerprint(record: Record, fields: list[str] | None = None) -> str:
    attributes = (
        record.attributes
        if fields is None
        else {
            key: {"present": key in record.attributes, "value": record.attributes.get(key)}
            for key in fields
        }
    )
    document = json.dumps(attributes, sort_keys=True, ensure_ascii=False, default=str)
    geometry = record.geometry.wkb if record.geometry is not None else b"null"
    return hashlib.sha256(document.encode() + b"\x00" + geometry).hexdigest()


def _custom_fails(record: Record, rule: ValidationRule, duplicate_count: int = 0) -> bool:
    present = rule.field in record.attributes
    value = record.attributes.get(rule.field) if rule.field is not None else None
    p = rule.parameters
    if rule.kind == "required":
        return not present or _null(value) or isinstance(value, str) and not value.strip()
    if rule.kind == "not_null":
        return not present or _null(value)
    if rule.kind == "type":
        dtype = p["dtype"]
        valid = {
            "string": isinstance(value, str),
            "integer": isinstance(value, int) and not isinstance(value, bool),
            "number": isinstance(value, (int, float))
            and not isinstance(value, bool)
            and (not isinstance(value, float) or math.isfinite(value)),
            "boolean": isinstance(value, bool),
            "object": isinstance(value, dict),
            "array": isinstance(value, list),
        }
        return not present or not valid[dtype]
    if rule.kind == "range":
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or isinstance(value, float)
            and not math.isfinite(value)
        ):
            return True
        return ("min" in p and value < p["min"]) or ("max" in p and value > p["max"])
    if rule.kind == "enum":
        return not present or not any(type(value) is type(v) and value == v for v in p["values"])
    if rule.kind == "duplicate_record":
        return duplicate_count > 1
    geometry = record.geometry
    if geometry is None or geometry.is_empty:
        return True
    coordinates = get_coordinates(geometry)
    return not np.isfinite(coordinates).all() or bool(
        np.any(coordinates[:, 0] < p["min_x"])
        or np.any(coordinates[:, 0] > p["max_x"])
        or np.any(coordinates[:, 1] < p["min_y"])
        or np.any(coordinates[:, 1] > p["max_y"])
    )


def validate(records: list[Record], config: Dataset) -> ValidationResult:
    """Never repair, drop, coerce or guess CRS; custom report-only rules preserve warnings."""
    if len({record.row_id for record in records}) != len(records):
        raise ValueError("Input row_id values must be unique for unambiguous reconciliation")
    issues: list[ValidationIssue] = []
    by_row: dict[int, list[ValidationIssue]] = {record.row_id: [] for record in records}

    def add(
        record, code, message, *, field=None, severity="ERROR", action="quarantine", reason=None
    ):
        issue = ValidationIssue(
            row_id=record.row_id,
            code=code,
            message=message,
            field=field,
            severity=severity,
            action="quarantine" if severity == "FATAL" else action,
            reason=reason or code,
        )
        by_row[record.row_id].append(issue)

    source_crs = None
    crs_failure = None
    try:
        source_crs = CRS.from_user_input(config.source_crs)
        target_crs = CRS.from_user_input(config.target_crs)
        if not (source_crs.is_geographic or source_crs.is_projected) or not (
            target_crs.is_geographic or target_crs.is_projected
        ):
            raise ValueError("Only horizontal geographic/projected CRSs are supported")
        Transformer.from_crs(source_crs, target_crs, always_xy=True)
        if config.format == "geojson" and source_crs.to_epsg() != 4326:
            raise ValueError("GeoJSON requires WGS84")
    except Exception:
        crs_failure = "crs_missing" if not config.source_crs else "crs_incompatible"
        source_crs = None
    keys = [str(r.attributes.get(config.id_column, "")).strip() for r in records]
    duplicate_ids = Counter(keys)
    fingerprints = [_fingerprint(record) for record in records]
    duplicate_records = Counter(fingerprints)
    custom_fingerprints = {
        rule.code: [_fingerprint(record, rule.parameters.get("fields")) for record in records]
        for rule in config.quality_rules
        if rule.kind == "duplicate_record"
    }
    custom_duplicates = {code: Counter(values) for code, values in custom_fingerprints.items()}
    for index, record in enumerate(records):
        for reason in sorted(set(record.errors)):
            add(
                record,
                reason.split(":", 1)[0],
                "Source parsing or normalization failed",
                reason=reason,
            )
        if crs_failure:
            add(
                record,
                crs_failure,
                "Source/target CRS is missing or incompatible",
                severity="FATAL",
            )
        for field in sorted({config.id_column, *config.required_columns}):
            value = record.attributes.get(field)
            if field not in record.attributes:
                add(
                    record,
                    "required_field",
                    "Required field is absent",
                    field=field,
                    reason=f"required_value:{field}",
                )
            elif _null(value):
                add(
                    record,
                    "null_value",
                    "Required value is null",
                    field=field,
                    reason=f"required_value:{field}",
                )
            elif isinstance(value, str) and not value.strip():
                add(
                    record,
                    "required_value",
                    "Required value is blank",
                    field=field,
                    reason=f"required_value:{field}",
                )
        if keys[index] and duplicate_ids[keys[index]] > 1:
            add(record, "duplicate_id", "Source identifier is not unique", field=config.id_column)
        if duplicate_records[fingerprints[index]] > 1:
            add(
                record,
                "duplicate_record",
                "Attributes and geometry exactly duplicate another record",
            )
        source_id = record.attributes.get(config.id_column)
        if isinstance(source_id, bool) or not isinstance(source_id, (str, int)):
            add(
                record,
                "invalid_source_identifier",
                "Identifier must be a string or integer",
                field=config.id_column,
            )
        geometry = record.geometry
        if geometry is None:
            add(record, "null_geometry", "Geometry is null", reason="missing_geometry")
        elif geometry.is_empty:
            add(record, "empty_geometry", "Geometry is empty", reason="missing_geometry")
        else:
            coords = get_coordinates(geometry, include_z=geometry.has_z)
            if not np.isfinite(coords).all():
                add(record, "nonfinite_coordinates", "Coordinates must be finite")
            elif (
                source_crs is not None
                and source_crs.is_geographic
                and (np.any(np.abs(coords[:, 0]) > 180) or np.any(np.abs(coords[:, 1]) > 90))
            ):
                add(record, "coordinates_out_of_range", "Longitude/latitude exceeds global limits")
            allowed = set(config.geometry_types)
            if config.geometry_normalization == "promote_multi":
                allowed |= {
                    "Multi" + kind for kind in config.geometry_types if not kind.startswith("Multi")
                }
            if geometry.geom_type not in allowed:
                add(
                    record,
                    "unexpected_geometry_type",
                    f"Unexpected geometry type: {geometry.geom_type}",
                )
            if not geometry.is_valid:
                detail = explain_validity(geometry)
                add(record, "invalid_geometry", detail, reason=f"invalid_geometry:{detail}")
        for rule in config.quality_rules:
            count = (
                custom_duplicates[rule.code][custom_fingerprints[rule.code][index]]
                if rule.kind == "duplicate_record"
                else 0
            )
            if _custom_fails(record, rule, count):
                add(
                    record,
                    rule.code,
                    f"Configured {rule.kind} rule failed",
                    field=rule.field,
                    severity=rule.severity,
                    action=rule.action,
                )
    accepted = []
    quarantined = []
    for record in records:
        failures = sorted(
            by_row[record.row_id], key=lambda issue: (issue.code, issue.field or "", issue.reason)
        )
        issues.extend(failures)
        if any(issue.action == "quarantine" for issue in failures):
            quarantined.append(
                QuarantinedRecord(
                    row_id=record.row_id,
                    record=record.raw,
                    issues=failures,
                    reasons=sorted({i.reason for i in failures}),
                )
            )
        else:
            accepted.append(
                {
                    **record.attributes,
                    "__row_id": record.row_id,
                    "__source_id": record.attributes[config.id_column],
                    "__source_feature_id": record.source_feature_id,
                    "__feature_id": str(
                        uuid5(
                            NAMESPACE_URL,
                            json.dumps(
                                ["geoflow", config.name, record.attributes[config.id_column]],
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                        )
                    ),
                    "geometry": record.geometry,
                }
            )
    frame = (
        gpd.GeoDataFrame(accepted, geometry="geometry", crs=source_crs)
        if accepted
        else gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs=source_crs)
    )
    # Per-rule counts count affected records, not multiple failed fields on one record.
    counts = Counter(
        code for row_issues in by_row.values() for code in {i.code for i in row_issues}
    )
    issue_free = sum(not row_issues for row_issues in by_row.values())
    summary = DatasetQualitySummary(
        input_records=len(records),
        accepted_records=len(frame),
        quarantined_records=len(quarantined),
        issue_free_records=issue_free,
        issue_count=len(issues),
        pass_rate=issue_free / len(records) if records else 0.0,
        rule_counts=dict(sorted(counts.items())),
        has_fatal=any(i.severity == "FATAL" for i in issues),
    )
    return ValidationResult(frame, quarantined, issues, summary)
