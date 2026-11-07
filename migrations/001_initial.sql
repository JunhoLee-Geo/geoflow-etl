-- Run explicitly with psql, or automatically on a NEW Compose data volume.
-- Existing volumes do not rerun docker-entrypoint-initdb.d scripts.
BEGIN;
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE SCHEMA IF NOT EXISTS geoflow;
CREATE TABLE IF NOT EXISTS geoflow._dataset_loads (
    dataset text PRIMARY KEY,
    run_id text NOT NULL,
    row_count bigint NOT NULL CHECK (row_count >= 0),
    srid integer NOT NULL,
    loaded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
COMMIT;
