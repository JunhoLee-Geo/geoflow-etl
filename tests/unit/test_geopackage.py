"""GeoPackage fixtures are synthetic and generated locally by GDAL/GeoPandas."""

import base64
import sqlite3
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import Point

from geoflow.config import Dataset
from geoflow.exceptions import IngestionError
from geoflow.ingestion.geopackage import ingest_geopackage
from geoflow.validation.quality import validate


def config(path: Path, **changes) -> Dataset:
    return Dataset(
        **{
            "name": "gpkg_fixture",
            "path": path,
            "format": "gpkg",
            "layer": "stops",
            "id_column": "code",
            "source_crs": "EPSG:4326",
            "required_columns": ["name"],
            "source_description": "Synthetic fixture",
            "license": "Synthetic test data",
            **changes,
        }
    )


@pytest.fixture
def gpkg(tmp_path):
    path = tmp_path / "fixture.gpkg"
    frame = gpd.GeoDataFrame(
        {"code": ["001", "002"], "name": ["One", "Two"]},
        geometry=[Point(-80.5, 43.5), Point(-80.4, 43.4)],
        crs=4326,
    )
    frame.to_file(path, layer="stops", driver="GPKG")
    return path


def mutate(path, sql, parameters=()):
    with sqlite3.connect(path) as connection:
        # GDAL's spatial-index triggers call extension functions unavailable in sqlite.
        triggers = connection.execute(
            "SELECT name FROM sqlite_schema WHERE type='trigger'"
        ).fetchall()
        for (name,) in triggers:
            connection.execute('DROP TRIGGER "' + name.replace('"', '""') + '"')
        connection.execute(sql, parameters)


def test_preserves_fid_attributes_coordinates_and_source_bytes(gpkg):
    content = gpkg.read_bytes()
    records = ingest_geopackage(content, config(gpkg))
    assert gpkg.read_bytes() == content
    assert [record.source_feature_id for record in records] == [1, 2]
    assert [record.attributes["code"] for record in records] == ["001", "002"]
    assert records[0].geometry.x == -80.5
    assert records[0].attributes["fid"] == 1
    assert not any(record.errors for record in records)
    assert records[0].raw["geom"]["encoding"] == "base64"


@pytest.mark.parametrize(
    "changes", [{"source_crs": "EPSG:26917"}, {"layer": "unknown"}, {"max_records": 1}]
)
def test_rejects_crs_layer_and_record_limit(gpkg, changes):
    with pytest.raises(IngestionError):
        ingest_geopackage(gpkg.read_bytes(), config(gpkg, **changes))


def test_malformed_geometry_retains_row_and_original_binary(gpkg):
    bad = b"not a geometry"
    mutate(gpkg, "UPDATE stops SET geom=? WHERE fid=2", (bad,))
    settings = config(gpkg)
    records = ingest_geopackage(gpkg.read_bytes(), settings)
    assert len(records) == 2
    assert records[1].source_feature_id == 2
    assert records[1].errors == ["invalid_geometry_encoding"]
    assert base64.b64decode(records[1].raw["geom"]["value"]) == bad
    result = validate(records, settings)
    assert len(result.accepted) == len(result.rejected) == 1


def test_null_geometry_is_not_discarded(gpkg):
    mutate(gpkg, "UPDATE stops SET geom=NULL WHERE fid=1")
    records = ingest_geopackage(gpkg.read_bytes(), config(gpkg))
    assert len(records) == 2
    assert records[0].geometry is None
    assert records[0].raw["geom"] is None


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE gpkg_spatial_ref_sys SET organization='NONE' WHERE srs_id=4326",
        "UPDATE gpkg_spatial_ref_sys SET definition='undefined' WHERE srs_id=4326",
        "DELETE FROM gpkg_geometry_columns WHERE table_name='stops'",
        "UPDATE gpkg_contents SET srs_id=-1 WHERE table_name='stops'",
    ],
)
def test_unknown_or_inconsistent_crs_metadata_is_not_guessed(gpkg, sql):
    mutate(gpkg, sql)
    with pytest.raises(IngestionError):
        ingest_geopackage(gpkg.read_bytes(), config(gpkg))


def test_temporary_directory_cleaned_on_success_and_failure(gpkg, monkeypatch, tmp_path):
    from tempfile import TemporaryDirectory

    import geoflow.ingestion.geopackage as module

    locations = []

    def temporary(*args, **kwargs):
        value = TemporaryDirectory(*args, dir=tmp_path, **kwargs)
        locations.append(Path(value.name))
        return value

    monkeypatch.setattr(module, "TemporaryDirectory", temporary)
    ingest_geopackage(gpkg.read_bytes(), config(gpkg))
    with pytest.raises(IngestionError):
        ingest_geopackage(gpkg.read_bytes(), config(gpkg, source_crs="EPSG:26917"))
    assert len(locations) == 2 and all(not path.exists() for path in locations)


def test_rejects_non_sqlite_input(tmp_path):
    with pytest.raises(IngestionError):
        ingest_geopackage(b"{}", config(tmp_path / "unused.gpkg"))


def test_geometry_header_crs_mismatch_quarantines_only_affected_feature(gpkg):
    import struct

    with sqlite3.connect(gpkg) as connection:
        blob = connection.execute("SELECT geom FROM stops WHERE fid=1").fetchone()[0]
    corrupt = blob[:4] + struct.pack("<i" if blob[3] & 1 else ">i", 26917) + blob[8:]
    mutate(gpkg, "UPDATE stops SET geom=? WHERE fid=1", (corrupt,))
    records = ingest_geopackage(gpkg.read_bytes(), config(gpkg))
    assert len(records) == 2
    assert records[0].errors == ["invalid_geometry_encoding"]
    assert records[1].errors == []


def test_feature_table_without_integer_primary_key_is_rejected(gpkg):
    mutate(gpkg, "ALTER TABLE stops RENAME TO old_stops")
    with sqlite3.connect(gpkg) as connection:
        connection.execute("CREATE TABLE stops AS SELECT * FROM old_stops")
    with pytest.raises(IngestionError, match="primary key"):
        ingest_geopackage(gpkg.read_bytes(), config(gpkg))


def test_view_cannot_masquerade_as_feature_layer(gpkg):
    mutate(gpkg, "ALTER TABLE stops RENAME TO old_stops")
    with sqlite3.connect(gpkg) as connection:
        connection.execute("CREATE VIEW stops AS SELECT * FROM old_stops")
    with pytest.raises(IngestionError, match="ordinary feature table"):
        ingest_geopackage(gpkg.read_bytes(), config(gpkg))
