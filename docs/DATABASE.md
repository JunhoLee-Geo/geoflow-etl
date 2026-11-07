# PostGIS run storage (0.4)

Run from the repository root in Windows 11 PowerShell. Install Docker Desktop
with Linux containers if using Compose. The verified local alternative is native
PostgreSQL with PostGIS. Docker is not installed on the development host.

```powershell
uv sync --frozen --extra dev
Copy-Item .env.example .env
# Set POSTGRES_PASSWORD in .env to a local development password.
docker compose up -d --wait
docker compose ps
# Set this process variable using your secret manager; URL-encode password characters.
$env:GEOFLOW_DATABASE_URL = 'postgresql+psycopg://geoflow:YOUR_URL_ENCODED_PASSWORD@localhost:5432/geoflow'
uv run --frozen --extra dev alembic upgrade head
uv run --frozen --extra dev alembic current
uv run --frozen --extra dev geoflow run configs/postgis.yml --dataset sample_points_postgis
```

The CLI and Alembic read process environment, not `.env`. Compose reads `.env`.
Optional Compose variables are POSTGRES_DB, POSTGRES_USER and POSTGRES_PORT;
defaults are geoflow, geoflow and 5432. The port binds to loopback only.
Use a privileged migration role for extension/schema creation and a separate
runtime role with SELECT/INSERT/DELETE on the seven tables. No credentials are
stored in YAML or alembic.ini. Never use a production database for tests.

Alembic owns the new schema on fresh and existing volumes. The older SQL init
script remains for compatibility and only bootstraps the legacy metadata table.
It does not replace `alembic upgrade head`. Existing legacy feature tables are
preserved; their historical data is not automatically imported. Downgrading the
new revision deletes its seven tables and is destructive; back up first.

## Schema

| Table | Purpose / key |
| --- | --- |
| datasets | Initial source configuration and metadata; name PK |
| source_snapshots | Recoverable raw bytes, SHA-256 and provenance; dataset/checksum unique |
| pipeline_runs | Terminal status, timestamps, effective config and snapshot FK; run_id PK |
| validated_features | Published spatial rows; (run_id,row_id) PK, unique run/feature UUID |
| quarantined_features | Original rejected record and errors; (run_id,row_id) PK |
| validation_issues | Every issue including report-only advisories; run/issue-number PK |
| pipeline_metrics | Counts, duration and quality summary; run_id PK/FK |

Validated geometry is explicitly stored as **2D geometry(Geometry,26917)**
(NAD83 / UTM zone 17N, metres), with a GiST index. Point, MultiPoint, LineString,
MultiLineString, Polygon and MultiPolygon can coexist. GeometryCollection, null,
empty and invalid geometries are rejected. The repository reprojects from the
validated frame's declared CRS and normalizes to 2D; immutable source bytes retain
original CRS, dimensions and identifiers. Processing target CRS can differ from
the fixed storage SRID. Changing storage SRID requires a reviewed migration.
Original typed source identifiers are JSONB; properties also retain source FIDs.

## Atomicity and repeat strategy

One SQLAlchemy transaction writes the dataset/snapshot, run, features, quarantine,
issues and metrics. An advisory transaction lock serializes a dataset's writers.
Database errors roll everything back. No accepted subset from a failed run is
published. Count reconciliation is checked before writing and after inserts;
metrics enforce input = accepted + quarantined.

The pipeline hashes source bytes, effective configuration and application version
into run_id. An identical successful rerun is a no-op. Changed bytes create a new
snapshot; changed config/version creates a new run that can reference the same
snapshot. Prior successful runs remain queryable. A reused run ID with different
source/configuration is refused. Identical failed-run retries replace that failed
audit atomically; this is run history, not a separate history of every retry.

Quality failures retain quarantine, all issues, raw snapshot and candidate counts,
but `published_records=0`. Accepted candidate counts are not published row counts.
Failures before validation store null reconciliation counts, not invented zeros.
The local failed report survives if a database audit cannot be written. Acquisition
failures before a source snapshot/run exists remain CLI failures. Database loading
failures roll back rather than attempt a second partial audit transaction.

Source bytes are limited to the configured bound (at most 100 MB) and stored in
BYTEA for recoverability. This is intentionally a bounded portfolio workload;
large production sources would need separately managed immutable object storage.
Filesystem reports and SQL commit are not a distributed transaction. A crash after
DB commit may leave a stale local report; an identical rerun safely reconciles.

## Real integration tests

Create a separate disposable database, set GEOFLOW_DATABASE_URL to it and apply
`alembic upgrade head`, then:

```powershell
$env:GEOFLOW_TEST_DATABASE_URL = $env:GEOFLOW_DATABASE_URL
uv run --frozen --extra dev pytest tests/integration -q
```

Tests create isolated names, test rollback using a real database constraint and
clean up their rows. Run against a dedicated test instance, not a shared service.
See VERIFICATION.md for actual results; CI configuration is not a claim of CI execution.

## Queries

```sql
-- Execution history and reconciliation, including failed runs.
SELECT r.dataset_name, r.run_id, r.status, r.started_at, r.finished_at,
       m.input_records, m.accepted_records, m.quarantined_records,
       m.published_records, m.duration_seconds
FROM geoflow.pipeline_runs r JOIN geoflow.pipeline_metrics m USING (run_id)
ORDER BY r.started_at DESC;

-- Per-rule affected record counts (multiple fields do not double-count records).
SELECT run_id, code, severity, count(DISTINCT row_id) AS affected_records
FROM geoflow.validation_issues GROUP BY run_id, code, severity;

-- Latest successfully published snapshot for each dataset.
WITH latest AS (
  SELECT DISTINCT ON (dataset_name) dataset_name, run_id
  FROM geoflow.pipeline_runs WHERE status = 'succeeded'
  ORDER BY dataset_name, finished_at DESC, run_id DESC
)
SELECT l.dataset_name, f.* FROM latest l
JOIN geoflow.validated_features f USING (run_id);
```

Migration conventions follow the [official Alembic tutorial](https://alembic.sqlalchemy.org/en/latest/tutorial.html).
