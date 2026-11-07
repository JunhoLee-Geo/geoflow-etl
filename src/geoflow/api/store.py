"""Bounded, parameterized read transactions against the monitoring schema."""

import json
import re
from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.engine import Engine


class MissingResource(Exception):
    pass


class DatabaseUnavailable(Exception):
    pass


RUN_SELECT = """SELECT r.run_id,r.dataset_name AS dataset_id,r.status,r.started_at,r.finished_at,
 m.duration_seconds,m.input_records,m.accepted_records,m.quarantined_records,m.published_records
 FROM geoflow.pipeline_runs r JOIN geoflow.pipeline_metrics m USING(run_id) """
DATASET_SELECT = """SELECT name AS dataset_id,created_at,
 configuration->>'source_crs' AS source_crs,configuration->>'target_crs' AS target_crs,
 configuration->'geometry_types' AS geometry_types,configuration->>'format' AS source_format
 FROM geoflow.datasets """
GEOMETRY_TYPES = {"Point", "MultiPoint", "LineString", "MultiLineString", "Polygon", "MultiPolygon"}


def _dataset(row):
    result = dict(row)
    for field in ("source_crs", "target_crs"):
        value = result[field]
        result[field] = (
            value if isinstance(value, str) and re.fullmatch(r"EPSG:\d{1,8}", value) else None
        )
    result["geometry_types"] = [v for v in (result["geometry_types"] or []) if v in GEOMETRY_TYPES]
    if result["source_format"] not in {"csv", "geojson", "gpkg"}:
        result["source_format"] = None
    return result


class MonitoringStore:
    def __init__(self, engine: Engine | None):
        self.engine = engine

    @contextmanager
    def connection(self):
        if self.engine is None:
            raise DatabaseUnavailable
        with self.engine.connect() as conn, conn.begin():
            # Even when configured with an overprivileged role, this transaction cannot write.
            conn.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            conn.execute(text("SET LOCAL statement_timeout = '3000ms'"))
            yield conn

    def health(self):
        with self.connection() as conn:
            if conn.scalar(text("SELECT to_regclass('geoflow.pipeline_runs')")) is None:
                raise DatabaseUnavailable
            conn.execute(text("SELECT run_id FROM geoflow.pipeline_runs LIMIT 0"))
        return {"status": "ready", "mode": "read_only"}

    def datasets(self, limit, offset):
        with self.connection() as conn:
            total = conn.scalar(text("SELECT count(*) FROM geoflow.datasets"))
            rows = conn.execute(
                text(DATASET_SELECT + "ORDER BY name LIMIT :limit OFFSET :offset"),
                {"limit": limit, "offset": offset},
            ).mappings()
            return [_dataset(row) for row in rows], total

    def runs(self, limit, offset, dataset_id=None):
        where = "WHERE r.dataset_name=:dataset_id " if dataset_id else ""
        parameters = {"limit": limit, "offset": offset, "dataset_id": dataset_id}
        with self.connection() as conn:
            total = conn.scalar(
                text("SELECT count(*) FROM geoflow.pipeline_runs r " + where), parameters
            )
            rows = conn.execute(
                text(
                    RUN_SELECT
                    + where
                    + "ORDER BY r.started_at DESC,r.run_id DESC LIMIT :limit OFFSET :offset"
                ),
                parameters,
            ).mappings()
            return [dict(row) for row in rows], total

    def run(self, run_id):
        with self.connection() as conn:
            row = (
                conn.execute(text(RUN_SELECT + "WHERE r.run_id=:id"), {"id": run_id})
                .mappings()
                .first()
            )
            if row is None:
                raise MissingResource
            return dict(row)

    def overview(self):
        with self.connection() as conn:
            return dict(
                conn.execute(
                    text("""SELECT count(*) AS total_runs,
              count(*) FILTER(WHERE r.status='succeeded') AS successful_runs,
              count(*) FILTER(WHERE r.status<>'succeeded') AS failed_runs,
              COALESCE(sum(m.input_records),0) AS input_records,
              COALESCE(sum(m.accepted_records),0) AS accepted_records,
              COALESCE(sum(m.quarantined_records),0) AS quarantined_records,
              COALESCE(sum(m.published_records),0) AS published_records,
              count(*) FILTER(WHERE m.input_records IS NULL) AS unmeasured_runs
              FROM geoflow.pipeline_runs r LEFT JOIN geoflow.pipeline_metrics m USING(run_id)
            """)
                )
                .mappings()
                .one()
            )

    def quality(self, run_id):
        with self.connection() as conn:
            row = (
                conn.execute(
                    text("""SELECT r.run_id,m.input_records,m.accepted_records,
              m.quarantined_records,(m.quality_summary->>'pass_rate')::float AS pass_rate
              FROM geoflow.pipeline_runs r JOIN geoflow.pipeline_metrics m USING(run_id)
              WHERE r.run_id=:id"""),
                    {"id": run_id},
                )
                .mappings()
                .first()
            )
            if row is None:
                raise MissingResource
            rules = conn.execute(
                text("""SELECT code,count(DISTINCT row_id) AS n
              FROM geoflow.validation_issues WHERE run_id=:id GROUP BY code ORDER BY code"""),
                {"id": run_id},
            ).all()
            severities = conn.execute(
                text("""SELECT severity,count(*) AS n
              FROM geoflow.validation_issues WHERE run_id=:id
              GROUP BY severity ORDER BY severity"""),
                {"id": run_id},
            ).all()
            return {**dict(row), "rule_counts": dict(rules), "severity_counts": dict(severities)}

    def issues(self, run_id, limit, offset):
        with self.connection() as conn:
            if not conn.scalar(
                text("SELECT 1 FROM geoflow.pipeline_runs WHERE run_id=:id"), {"id": run_id}
            ):
                raise MissingResource
            total = conn.scalar(
                text("SELECT count(*) FROM geoflow.validation_issues WHERE run_id=:id"),
                {"id": run_id},
            )
            rows = conn.execute(
                text("""SELECT issue_number,code,severity FROM geoflow.validation_issues
              WHERE run_id=:id ORDER BY issue_number LIMIT :limit OFFSET :offset"""),
                {"id": run_id, "limit": limit, "offset": offset},
            ).mappings()
            return [dict(row) for row in rows], total

    def summary(self, dataset_id):
        with self.connection() as conn:
            row = (
                conn.execute(text(DATASET_SELECT + "WHERE name=:id"), {"id": dataset_id})
                .mappings()
                .first()
            )
            if row is None:
                raise MissingResource
            counts = (
                conn.execute(
                    text("""SELECT count(*) AS total_runs,
              count(*) FILTER(WHERE status='succeeded') AS successful_runs,
              count(*) FILTER(WHERE status<>'succeeded') AS failed_runs
              FROM geoflow.pipeline_runs WHERE dataset_name=:id"""),
                    {"id": dataset_id},
                )
                .mappings()
                .one()
            )
            latest = conn.scalar(
                text("""SELECT run_id FROM geoflow.pipeline_runs
              WHERE dataset_name=:id ORDER BY started_at DESC,run_id DESC LIMIT 1"""),
                {"id": dataset_id},
            )
            return {**_dataset(row), **dict(counts), "latest_run_id": latest}

    def preview(self, dataset_id, limit):
        with self.connection() as conn:
            if not conn.scalar(
                text("SELECT 1 FROM geoflow.datasets WHERE name=:id"), {"id": dataset_id}
            ):
                raise MissingResource
            run_id = conn.scalar(
                text("""SELECT run_id FROM geoflow.pipeline_runs
              WHERE dataset_name=:id AND status='succeeded'
              ORDER BY finished_at DESC,run_id DESC LIMIT 1"""),
                {"id": dataset_id},
            )
            rows = []
            if run_id:
                rows = (
                    conn.execute(
                        text("""WITH sampled AS MATERIALIZED (
                  SELECT geometry FROM geoflow.validated_features WHERE run_id=:id
                  ORDER BY row_id LIMIT :limit
                ), simplified AS MATERIALIZED (
                  SELECT CASE WHEN ST_NPoints(geometry)<=10000
                    THEN ST_SimplifyPreserveTopology(geometry,25) END AS geometry FROM sampled
                ) SELECT CASE WHEN ST_NPoints(geometry)<=1000
                    THEN ST_AsGeoJSON(ST_Transform(geometry,4326),6) END AS geometry
                  FROM simplified"""),
                        {"id": run_id, "limit": limit},
                    )
                    .scalars()
                    .all()
                )
            return bounded_preview(dataset_id, run_id, rows, limit)


def bounded_preview(dataset_id, run_id, geometries, limit):
    """No identifiers/properties; cap serialized geometry bytes before response creation."""
    features = []
    size = 0
    for value in geometries[:100]:
        if value is None or len(value.encode("utf-8")) > 90000 - size:
            continue
        geometry = json.loads(value)
        if geometry.get("type") not in GEOMETRY_TYPES:
            continue
        size += len(value.encode("utf-8"))
        features.append({"type": "Feature", "geometry": geometry, "properties": {}})
    return {
        "dataset_id": dataset_id,
        "run_id": run_id,
        "geojson": {"type": "FeatureCollection", "features": features},
        "sampled_features": len(geometries),
        "returned_features": len(features),
        "omitted_features": len(geometries) - len(features),
        "limit": limit,
        "storage_crs": "EPSG:26917",
        "preview_crs": "EPSG:4326",
        "simplification_tolerance_m": 25,
    }
