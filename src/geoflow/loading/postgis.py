"""Transactional PostGIS sink; schema setup is an explicit migration step."""

from __future__ import annotations

import json
import re
from typing import Any

import geopandas as gpd
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError


class PostGISLoadError(RuntimeError):
    """The PostGIS snapshot could not be loaded safely."""


def load_postgis(frame: gpd.GeoDataFrame, *, database_url: str, table: str, run_id: str) -> int:
    """Atomically replace one dataset snapshot and update its latest-load metadata.

    Repeated runs replace rather than append. An advisory transaction lock serializes
    concurrent loads for the same dataset. Any database error rolls back both the
    replacement and metadata, preserving the previous committed snapshot.
    """
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,47}", table):
        raise PostGISLoadError(
            "Table name must start with a lowercase letter and use at most "
            "48 lowercase letters, digits or underscores."
        )
    if not run_id or len(run_id) > 128:
        raise PostGISLoadError("run_id must contain between 1 and 128 characters.")
    if frame.crs is None or (srid := frame.crs.to_epsg()) is None:
        raise PostGISLoadError("The validated frame must have an EPSG-identifiable CRS.")
    if (
        frame.geometry.isna().any()
        or frame.geometry.is_empty.any()
        or not frame.geometry.is_valid.all()
    ):
        raise PostGISLoadError("All rows must have nonempty, valid geometry before loading.")
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    if not database_url.startswith("postgresql+psycopg://"):
        raise PostGISLoadError("Use a PostgreSQL URL with the psycopg driver.")

    # Pandas handles NumPy scalars, nulls and timestamps deterministically. Original
    # source bytes remain in the pipeline's immutable provenance archive.
    try:
        properties: list[dict[str, Any]] = json.loads(
            frame.drop(columns=frame.geometry.name).to_json(orient="records", date_format="iso")
        )
        rows = [
            {
                "record_id": index,
                "properties": json.dumps(values, allow_nan=False),
                "wkb": geometry.wkb,
                "srid": srid,
                "run_id": run_id,
            }
            for index, (values, geometry) in enumerate(
                zip(properties, frame.geometry, strict=True), 1
            )
        ]
    except (TypeError, ValueError, OverflowError) as exc:
        raise PostGISLoadError(
            "Attributes or geometry could not be serialized for PostGIS."
        ) from exc

    # Identifiers cannot be SQL parameters; strict allowlisting above makes these
    # quoted names safe. All record values are bound parameters.
    qualified = f'geoflow."{table}"'
    engine = None
    try:
        engine = create_engine(database_url, pool_pre_ping=True)
        with engine.begin() as connection:
            initialized = connection.scalar(text("SELECT to_regclass('geoflow._dataset_loads')"))
            if initialized is None:
                raise PostGISLoadError("Apply migrations/001_initial.sql before loading PostGIS.")
            connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:dataset, 0))"),
                {"dataset": f"geoflow.{table}"},
            )
            connection.execute(
                text(f"""CREATE TABLE IF NOT EXISTS {qualified} (
                record_id bigint PRIMARY KEY,
                properties jsonb NOT NULL,
                geometry geometry(Geometry, {int(srid)}) NOT NULL,
                run_id text NOT NULL
            )""")
            )
            existing_srid = connection.scalar(
                text("SELECT Find_SRID('geoflow', :table, 'geometry')"), {"table": table}
            )
            if existing_srid != srid:
                raise PostGISLoadError(
                    "The existing dataset has a different CRS; "
                    "migrate it explicitly before loading."
                )
            connection.execute(
                text(
                    f'CREATE INDEX IF NOT EXISTS "{table}_geometry_gist" '
                    f"ON {qualified} USING gist (geometry)"
                )
            )
            connection.execute(text(f"DELETE FROM {qualified}"))
            if rows:
                connection.execute(
                    text(f"""INSERT INTO {qualified} (record_id, properties, geometry, run_id)
                        VALUES (:record_id, CAST(:properties AS jsonb),
                        ST_GeomFromWKB(:wkb, :srid), :run_id)"""),
                    rows,
                )
            connection.execute(
                text("""INSERT INTO geoflow._dataset_loads (dataset, run_id, row_count, srid)
                    VALUES (:dataset, :run_id, :row_count, :srid)
                    ON CONFLICT (dataset) DO UPDATE SET run_id = EXCLUDED.run_id,
                    row_count = EXCLUDED.row_count, srid = EXCLUDED.srid,
                    loaded_at = clock_timestamp()"""),
                {"dataset": table, "run_id": run_id, "row_count": len(rows), "srid": srid},
            )
    except (SQLAlchemyError, ValueError):
        # Connection strings may contain credentials. Keep driver details out of
        # normal CLI errors; transaction context has already rolled back.
        raise PostGISLoadError(
            "PostGIS load failed; the transaction was rolled back. "
            "Check database availability, migrations and dataset schema."
        ) from None
    finally:
        if engine is not None:
            engine.dispose()
    return len(rows)
