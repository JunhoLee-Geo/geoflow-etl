"""Boundary cases for typed rules and deterministic disposition."""

import pytest
from pydantic import ValidationError
from shapely.geometry import Point

from geoflow.config import Dataset
from geoflow.ingestion.readers import Record
from geoflow.validation.models import ValidationRule
from geoflow.validation.quality import validate


def config(**kwargs):
    return Dataset(
        name="edges",
        path="unused.csv",
        format="csv",
        id_column="id",
        source_crs="EPSG:4326",
        source_description="Synthetic",
        license="Synthetic",
        **kwargs,
    )


def record(attributes=None, geometry=None):
    attrs = {"id": "A", **(attributes or {})}
    return Record(1, attrs.copy(), attrs, geometry if geometry is not None else Point(0, 0), [])


def test_empty_geometry_is_separate_from_null():
    row = record(geometry=Point())
    result = validate([row], config())
    assert [i.code for i in result.issues] == ["empty_geometry"]
    assert result.summary.quarantined_records == 1


def test_fatal_report_policy_still_quarantines():
    cfg = config(
        quality_rules=[
            dict(
                code="custom_required",
                kind="required",
                field="name",
                severity="FATAL",
                action="report",
            )
        ]
    )
    result = validate([record()], cfg)
    assert result.summary.has_fatal
    assert result.summary.accepted_records == 0
    assert result.issues[0].action == "quarantine"


def test_null_required_and_enum_retain_multiple_issues():
    cfg = config(
        quality_rules=[
            dict(code="custom_null", kind="not_null", field="name"),
            dict(code="custom_required", kind="required", field="name"),
            dict(code="custom_enum", kind="enum", field="state", parameters={"values": [1]}),
        ]
    )
    result = validate([record({"name": None, "state": True})], cfg)
    assert [i.code for i in result.issues] == ["custom_enum", "custom_null", "custom_required"]
    assert result.summary.quarantined_records == 1
    assert result.summary.issue_count == 3


def test_crs_bypass_is_fatal_without_assigning_a_guess():
    cfg = config().model_copy(update={"source_crs": None})
    result = validate([record()], cfg)
    assert result.summary.has_fatal
    assert result.issues[0].code == "crs_missing"
    assert result.accepted.crs is None


def test_incompatible_geocentric_crs_is_fatal():
    cfg = config().model_copy(update={"target_crs": "EPSG:4978"})
    result = validate([record()], cfg)
    assert result.summary.has_fatal
    assert result.issues[0].code == "crs_incompatible"


@pytest.mark.parametrize(
    "kind,parameters",
    [
        ("range", {"min": 2, "max": 1}),
        ("range", {"min": float("nan")}),
        ("type", {"dtype": "eval"}),
        ("enum", {"values": []}),
        ("bounds", {"min_x": 0}),
        ("duplicate_record", {"fields": ["x", "x"]}),
    ],
)
def test_bad_parameters_are_rejected(kind, parameters):
    with pytest.raises(ValidationError):
        ValidationRule(
            code="custom_test",
            kind=kind,
            parameters=parameters,
            field="value" if kind in {"range", "type", "enum"} else None,
        )
