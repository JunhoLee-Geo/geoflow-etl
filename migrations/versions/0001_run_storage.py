"""Frozen initial run-history schema, independent of future ORM changes."""

from alembic import op

revision = "0001_run_storage"
down_revision = None
branch_labels = None
depends_on = None

UPGRADE_SQL = [
    """CREATE TABLE geoflow.datasets (
        name VARCHAR(48) NOT NULL,
        configuration JSONB NOT NULL,
        source_metadata JSONB NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        PRIMARY KEY (name)
)""",
    """CREATE TABLE geoflow.source_snapshots (
        id VARCHAR(64) NOT NULL,
        dataset_name VARCHAR(48) NOT NULL,
        sha256 VARCHAR(64) NOT NULL,
        raw_bytes BYTEA NOT NULL,
        provenance JSONB NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        PRIMARY KEY (id),
        UNIQUE (dataset_name, sha256),
        UNIQUE (dataset_name, id),
        CHECK (octet_length(raw_bytes) <= 100000000),
        FOREIGN KEY(dataset_name) REFERENCES geoflow.datasets (name)
)""",
    """CREATE TABLE geoflow.pipeline_runs (
        run_id VARCHAR(128) NOT NULL,
        dataset_name VARCHAR(48) NOT NULL,
        snapshot_id VARCHAR(64) NOT NULL,
        configuration JSONB NOT NULL,
        status VARCHAR(20) NOT NULL,
        error_type VARCHAR(128),
        started_at TIMESTAMP WITH TIME ZONE NOT NULL,
        finished_at TIMESTAMP WITH TIME ZONE NOT NULL,
        PRIMARY KEY (run_id),
        FOREIGN KEY(dataset_name, snapshot_id) REFERENCES geoflow.source_snapshots
(dataset_name, id),
        CHECK (status IN ('succeeded','quality_failed','failed')),
        FOREIGN KEY(dataset_name) REFERENCES geoflow.datasets (name)
)""",
    """CREATE TABLE geoflow.pipeline_metrics (
        run_id VARCHAR(128) NOT NULL,
        input_records BIGINT,
        accepted_records BIGINT,
        quarantined_records BIGINT,
        published_records BIGINT NOT NULL,
        duration_seconds FLOAT NOT NULL,
        quality_summary JSONB,
        PRIMARY KEY (run_id),
        CHECK (input_records = accepted_records + quarantined_records),
    CHECK ((input_records IS NULL AND accepted_records IS NULL AND quarantined_records IS NULL)
        OR (input_records IS NOT NULL AND accepted_records IS NOT NULL
            AND quarantined_records IS NOT NULL)),
    CHECK (accepted_records IS NULL OR published_records <= accepted_records),
        CHECK (input_records >= 0 AND accepted_records >= 0 AND quarantined_records >=
0),
        CHECK (published_records >= 0 AND duration_seconds >= 0),
        FOREIGN KEY(run_id) REFERENCES geoflow.pipeline_runs (run_id) ON DELETE CASCADE
)""",
    """CREATE TABLE geoflow.quarantined_features (
        run_id VARCHAR(128) NOT NULL,
        row_id BIGINT NOT NULL,
        raw_record JSONB NOT NULL,
        error_metadata JSONB NOT NULL,
        PRIMARY KEY (run_id, row_id),
        FOREIGN KEY(run_id) REFERENCES geoflow.pipeline_runs (run_id) ON DELETE CASCADE
)""",
    """CREATE TABLE geoflow.validated_features (
        run_id VARCHAR(128) NOT NULL,
        row_id BIGINT NOT NULL,
        feature_id VARCHAR(36) NOT NULL,
        source_identifier JSONB NOT NULL,
        properties JSONB NOT NULL,
        geometry geometry(Geometry,26917) NOT NULL,
        PRIMARY KEY (run_id, row_id),
        UNIQUE (run_id, feature_id),
        CHECK (ST_IsValid(geometry) AND NOT ST_IsEmpty(geometry)),
        CHECK (GeometryType(geometry) IN
('POINT','MULTIPOINT','LINESTRING','MULTILINESTRING','POLYGON','MULTIPOLYGON')),
        FOREIGN KEY(run_id) REFERENCES geoflow.pipeline_runs (run_id) ON DELETE CASCADE
)""",
    """CREATE TABLE geoflow.validation_issues (
        run_id VARCHAR(128) NOT NULL,
        issue_number INTEGER NOT NULL,
        row_id BIGINT NOT NULL,
        code VARCHAR(128) NOT NULL,
        severity VARCHAR(10) NOT NULL,
        details JSONB NOT NULL,
        PRIMARY KEY (run_id, issue_number),
        CHECK (severity IN ('INFO','WARNING','ERROR','FATAL')),
        FOREIGN KEY(run_id) REFERENCES geoflow.pipeline_runs (run_id) ON DELETE CASCADE
)""",
    """CREATE INDEX validated_features_geometry_gist ON geoflow.validated_features USING gist
(geometry)""",
    """CREATE INDEX validation_issues_run_code_idx ON geoflow.validation_issues (run_id, code)""",
]
DOWNGRADE_SQL = [
    """DROP TABLE geoflow.validation_issues""",
    """DROP TABLE geoflow.validated_features""",
    """DROP TABLE geoflow.quarantined_features""",
    """DROP TABLE geoflow.pipeline_metrics""",
    """DROP TABLE geoflow.pipeline_runs""",
    """DROP TABLE geoflow.source_snapshots""",
    """DROP TABLE geoflow.datasets""",
]


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.execute("CREATE SCHEMA IF NOT EXISTS geoflow")
    op.execute("""CREATE TABLE IF NOT EXISTS geoflow._dataset_loads (
        dataset text PRIMARY KEY, run_id text NOT NULL,
        row_count bigint NOT NULL CHECK (row_count >= 0), srid integer NOT NULL,
        loaded_at timestamptz NOT NULL DEFAULT clock_timestamp())""")
    for statement in UPGRADE_SQL:
        op.execute(statement)


def downgrade():
    for statement in DOWNGRADE_SQL:
        op.execute(statement)
