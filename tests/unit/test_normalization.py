import json

import pytest
from pydantic import ValidationError

from geoflow.config import Dataset
from geoflow.exceptions import ConfigError, IngestionError
from geoflow.ingestion.readers import ingest
from geoflow.orchestration.pipeline import run_dataset
from geoflow.transformation.normalization import normalize_records
from geoflow.validation.quality import validate


def config(tmp_path, **changes):
    values = dict(
        name="normalized",
        path=tmp_path / "points.csv",
        format="csv",
        id_column="id",
        source_crs="EPSG:4326",
        source_description="Synthetic",
        license="Synthetic fixture",
    )
    values.update(changes)
    return Dataset(**values)


def test_normalization_preserves_raw_and_identifier(tmp_path):
    cfg = config(
        tmp_path,
        field_mapping={"name": "label"},
        trim_fields=["label"],
        geometry_normalization="promote_multi",
    )
    content = b"id,name,longitude,latitude\n001, Test ,-80.5,43.4\n"
    original = ingest(content, cfg)
    records, history, warnings = normalize_records(original, cfg)
    assert records[0].raw == original[0].raw
    assert original[0].attributes["name"] == " Test "
    assert records[0].attributes["label"] == "Test"
    assert records[0].geometry.geom_type == "MultiPoint"
    first = validate(records, cfg).accepted.iloc[0]
    second = validate(ingest(content, cfg), cfg).accepted.iloc[0]
    assert first["__source_id"] == "001"
    assert first["__feature_id"] == second["__feature_id"]
    assert history[-1]["changed_features"] == 1 and warnings


def test_mapping_collision_fails_without_overwriting(tmp_path):
    cfg = config(tmp_path, field_mapping={"name": "longitude"})
    records = ingest(b"id,name,longitude,latitude\nA,Test,-80,43\n", cfg)
    with pytest.raises(ConfigError):
        normalize_records(records, cfg)
    assert records[0].attributes["longitude"] == "-80"


def test_missing_mapped_field_is_quarantined(tmp_path):
    cfg = config(tmp_path, field_mapping={"absent": "label"})
    records, _, _ = normalize_records(ingest(b"id,longitude,latitude\nA,-80,43\n", cfg), cfg)
    result = validate(records, cfg)
    assert len(result.rejected) == 1
    assert "mapping_source_missing:absent" in records[0].errors


def test_existing_raw_snapshot_is_never_overwritten(tmp_path):
    cfg = config(tmp_path)
    cfg.path.write_bytes(b"id,longitude,latitude\nA,-80,43\n")
    report = run_dataset(cfg, tmp_path / "out")
    raw = report.parent / "source.csv"
    raw.write_bytes(b"externally changed")
    with pytest.raises(IngestionError, match="refusing to overwrite"):
        run_dataset(cfg, tmp_path / "out")
    assert raw.read_bytes() == b"externally changed"


def test_geojson_feature_id_and_crs_history(tmp_path):
    cfg = config(tmp_path, format="geojson")
    cfg.path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "id": "source-007",
                        "properties": {"id": "001"},
                        "geometry": {"type": "Point", "coordinates": [-80.5, 43.4]},
                    }
                ],
            }
        )
    )
    path = run_dataset(cfg, tmp_path / "out")
    report = json.loads(path.read_text())
    feature = json.loads((path.parent / "accepted.geojson").read_text())["features"][0]
    assert feature["properties"]["__source_feature_id"] == "source-007"
    assert feature["id"] == feature["properties"]["__feature_id"]
    assert report["source_crs"] == "EPSG:4326"
    assert report["target_crs"] == "EPSG:26917"


@pytest.mark.parametrize(
    "changes",
    [
        dict(source_crs=None),
        dict(path=None, url="http://example.test/data"),
        dict(path=None, url="https://example.test/data?token=secret"),
        dict(trim_fields=["id"]),
    ],
)
def test_config_rejects_unknown_crs_unsafe_url_or_identifier_changes(tmp_path, changes):
    with pytest.raises(ValidationError):
        config(tmp_path, **changes)
