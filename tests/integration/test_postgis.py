"""Opt-in database tests. Run only against a disposable, migrated test database."""

from __future__ import annotations

import os
from collections.abc import Iterator
from uuid import uuid4

import geopandas as gpd
import pytest
from shapely.geometry import Point
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from geoflow.loading.postgis import PostGISLoadError, load_postgis

pytestmark = pytest.mark.integration


@pytest.fixture
def database() -> Iterator[tuple[str, Engine, str]]:
    url = os.getenv("GEOFLOW_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set GEOFLOW_TEST_DATABASE_URL to a disposable migrated PostGIS database.")
    url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_engine(url)
    table = f"test_{uuid4().hex}"
    try:
        with engine.connect() as connection:
            initialized = connection.scalar(text("SELECT to_regclass('geoflow._dataset_loads')"))
            assert initialized, "Apply migrations/001_initial.sql to the test database."
        yield url, engine, table
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP TABLE IF EXISTS geoflow."{table}"'))
            connection.execute(
                text("DELETE FROM geoflow._dataset_loads WHERE dataset = :dataset"),
                {"dataset": table},
            )
        engine.dispose()


def sample() -> gpd.GeoDataFrame:
    # Deliberately synthetic; never represents analytical project output.
    return gpd.GeoDataFrame(
        {"source_id": ["fixture-a", "fixture-b"], "value": [1, 2]},
        geometry=[Point(1, 2), Point(3, 4)],
        crs="EPSG:4326",
    )


def metadata(engine: Engine, table: str) -> dict:
    with engine.connect() as connection:
        result = (
            connection.execute(
                text("SELECT * FROM geoflow._dataset_loads WHERE dataset = :dataset"),
                {"dataset": table},
            )
            .mappings()
            .one()
        )
        return dict(result)


def test_repeated_load_is_idempotent_and_has_spatial_index(
    database: tuple[str, Engine, str],
) -> None:
    url, engine, table = database
    assert load_postgis(sample(), database_url=url, table=table, run_id="run-first") == 2
    assert load_postgis(sample(), database_url=url, table=table, run_id="run-repeat") == 2
    with engine.connect() as connection:
        assert connection.scalar(text(f'SELECT count(*) FROM geoflow."{table}"')) == 2
        assert connection.scalar(
            text(
                "SELECT bool_and(ST_SRID(geometry) = 4326 AND ST_IsValid(geometry)) "
                f'FROM geoflow."{table}"'
            )
        )
        indexes = connection.scalar(
            text(
                "SELECT count(*) FROM pg_indexes WHERE schemaname = 'geoflow' "
                "AND tablename = :table AND indexdef LIKE '%USING gist%'"
            ),
            {"table": table},
        )
        assert indexes == 1
    assert metadata(engine, table)["run_id"] == "run-repeat"


def test_database_error_rolls_back_replacement_and_metadata(
    database: tuple[str, Engine, str],
) -> None:
    url, engine, table = database
    load_postgis(sample(), database_url=url, table=table, run_id="committed")
    with engine.begin() as connection:
        connection.execute(
            text(
                f'ALTER TABLE geoflow."{table}" ADD CONSTRAINT reject_test_run '
                "CHECK (run_id <> 'rejected')"
            )
        )
    with pytest.raises(PostGISLoadError, match="rolled back"):
        load_postgis(sample(), database_url=url, table=table, run_id="rejected")
    with engine.connect() as connection:
        assert connection.scalar(text(f'SELECT count(*) FROM geoflow."{table}"')) == 2
        assert connection.scalar(text(f'SELECT min(run_id) FROM geoflow."{table}"')) == "committed"
    assert metadata(engine, table)["run_id"] == "committed"


def test_empty_validated_snapshot_replaces_previous_rows(
    database: tuple[str, Engine, str],
) -> None:
    url, engine, table = database
    load_postgis(sample(), database_url=url, table=table, run_id="populated")
    assert load_postgis(sample().iloc[:0], database_url=url, table=table, run_id="empty") == 0
    with engine.connect() as connection:
        assert connection.scalar(text(f'SELECT count(*) FROM geoflow."{table}"')) == 0
    assert metadata(engine, table)["row_count"] == 0


def test_crs_change_requires_explicit_migration(database: tuple[str, Engine, str]) -> None:
    url, engine, table = database
    load_postgis(sample(), database_url=url, table=table, run_id="original-crs")
    with pytest.raises(PostGISLoadError, match="different CRS"):
        load_postgis(
            sample().to_crs(3857).iloc[:0], database_url=url, table=table, run_id="new-crs"
        )
    with engine.connect() as connection:
        assert connection.scalar(text(f'SELECT count(*) FROM geoflow."{table}"')) == 2
    assert metadata(engine, table)["srid"] == 4326
