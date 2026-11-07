"""Record preservation and spatial validation using synthetic data only."""

import json

import pytest

from geoflow.config import Dataset
from geoflow.exceptions import IngestionError
from geoflow.ingestion.readers import ingest
from geoflow.validation.quality import validate


def config(**overrides):
    return Dataset(
        **{
            "name": "synthetic",
            "path": "unused.csv",
            "format": "csv",
            "id_column": "id",
            "required_columns": ["name"],
            "source_crs": "EPSG:4326",
            "source_description": "Synthetic unit-test points",
            "license": "Synthetic test fixture",
            **overrides,
        }
    )


def csv_quality(rows, **overrides):
    content = ("id,name,longitude,latitude\n" + "\n".join(rows) + "\n").encode()
    settings = config(**overrides)
    records = ingest(content, settings)
    return records, validate(records, settings)


def feature(identifier, geometry, properties=None):
    return {
        "type": "Feature",
        "properties": properties
        if properties is not None
        else {"id": identifier, "name": f"Synthetic {identifier}"},
        "geometry": geometry,
    }


def geojson_quality(features, **overrides):
    content = json.dumps({"type": "FeatureCollection", "features": features}).encode()
    settings = config(format="geojson", **overrides)
    records = ingest(content, settings)
    return records, validate(records, settings)


def test_csv_quarantine_preserves_original_values_row_numbers_and_all_records():
    records, result = csv_quality(
        [
            "A,Valid,-80,43",
            "B,Invalid number,oops,43",
            "C,Out of bounds,181,43",
            "D,Short row,-80",
            "E,Extra cells,-80,43,extra",
        ]
    )
    assert len(records) == len(result.accepted) + len(result.rejected) == 5
    assert result.accepted["id"].tolist() == ["A"]
    by_row = {row["row_id"]: row for row in result.rejected}
    assert by_row[2]["record"]["longitude"] == "oops"
    assert "invalid_coordinates" in by_row[2]["reasons"]
    assert "coordinates_out_of_range" in by_row[3]["reasons"]
    assert "malformed_csv_row" in by_row[4]["reasons"]
    assert by_row[5]["record"]["__extra_cells"] == ["extra"]
    assert "malformed_csv_row" in by_row[5]["reasons"]


def test_every_duplicate_id_is_quarantined_not_arbitrarily_deduplicated():
    _, result = csv_quality(["A,First,-80,43", " A ,Second,-81,44", "B,Unique,-82,45"])
    assert result.accepted["id"].tolist() == ["B"]
    assert [row["row_id"] for row in result.rejected] == [1, 2]
    assert all("duplicate_id" in row["reasons"] for row in result.rejected)
    assert result.counts["duplicate_id"] == 2


def test_required_values_reject_blank_and_whitespace_fields():
    _, result = csv_quality(["A,,-80,43", "B,   ,-80,43", ",Missing ID,-80,43"])
    assert result.accepted.empty
    assert "required_value:name" in result.rejected[0]["reasons"]
    assert "required_value:name" in result.rejected[1]["reasons"]
    assert "required_value:id" in result.rejected[2]["reasons"]


@pytest.mark.parametrize("coordinate", ["nan", "inf", "-inf"])
def test_nonfinite_csv_coordinates_are_quarantined(coordinate):
    records, result = csv_quality([f"A,Nonfinite,{coordinate},43"])
    assert len(records) == len(result.rejected) == 1
    assert result.accepted.empty
    assert "nonfinite_coordinates" in result.rejected[0]["reasons"]
    assert result.rejected[0]["record"]["longitude"] == coordinate


@pytest.mark.parametrize(
    "content",
    [
        b"id,name,longitude,latitude\n",
        b"id,name,longitude,longitude,latitude\nA,a,0,0,0\n",
        b"id,name,latitude\nA,a,0\n",
        b"id,name,longitude,latitude,__private\nA,a,0,0,x\n",
    ],
)
def test_empty_or_ambiguous_csv_fails_without_silently_reinterpreting_headers(content):
    with pytest.raises(IngestionError):
        ingest(content, config())


def test_missing_null_and_nonfeature_geojson_records_are_retained_in_quarantine():
    records, result = geojson_quality(
        [
            feature("A", {"type": "Point", "coordinates": [0, 0]}),
            feature("B", None),
            {"type": "Feature", "properties": {"id": "C", "name": "Missing"}},
            None,
        ]
    )
    assert len(records) == len(result.accepted) + len(result.rejected) == 4
    assert result.accepted["id"].tolist() == ["A"]
    assert [row["row_id"] for row in result.rejected] == [2, 3, 4]
    assert all("missing_geometry" in row["reasons"] for row in result.rejected)
    assert result.rejected[-1]["record"] == {"__source_value": None}


def test_invalid_polygon_topology_is_quarantined_without_repair():
    bowtie = {"type": "Polygon", "coordinates": [[[0, 0], [1, 1], [1, 0], [0, 1], [0, 0]]]}
    _, result = geojson_quality([feature("A", bowtie)], geometry_types=["Polygon"])
    assert result.accepted.empty
    assert result.rejected[0]["record"]["geometry"] == bowtie
    assert any(reason.startswith("invalid_geometry:") for reason in result.rejected[0]["reasons"])


def test_required_geojson_properties_missing_or_null_are_rejected():
    point = {"type": "Point", "coordinates": [0, 0]}
    _, result = geojson_quality(
        [
            feature("A", point, {"id": "A"}),
            feature("B", point, {"id": "B", "name": None}),
        ]
    )
    assert result.accepted.empty
    assert all("required_value:name" in row["reasons"] for row in result.rejected)


@pytest.mark.parametrize(
    "geometry",
    [
        {"type": "UnknownGeometry", "coordinates": [0, 0]},
        {"type": "Point", "coordinates": []},
        {"type": "LineString", "coordinates": [[0, 0]]},
    ],
)
def test_malformed_geojson_geometry_is_quarantined_per_record(geometry):
    records, result = geojson_quality([feature("A", geometry)])
    assert len(records) == len(result.rejected) == 1
    assert result.accepted.empty


@pytest.mark.parametrize(
    "document",
    [
        {"type": "FeatureCollection", "features": []},
        {"type": "FeatureCollection", "features": None},
        {"type": "FeatureCollection", "features": [], "crs": {}},
    ],
)
def test_invalid_or_empty_collection_fails_explicitly(document):
    with pytest.raises(IngestionError):
        ingest(json.dumps(document).encode(), config(format="geojson"))


def test_source_record_limit_fails_instead_of_truncating():
    with pytest.raises(IngestionError, match="limit"):
        csv_quality(["A,a,0,0", "B,b,1,1"], max_records=1)


def test_unclosed_geojson_polygon_is_not_silently_closed_by_shapely():
    ring = [[0, 0], [1, 0], [1, 1], [0, 1]]
    _, result = geojson_quality(
        [feature("A", {"type": "Polygon", "coordinates": [ring]})],
        geometry_types=["Polygon"],
    )
    assert result.accepted.empty
    assert result.rejected[0]["record"]["geometry"]["coordinates"] == [ring]
