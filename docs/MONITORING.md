# Read-only monitoring

GeoFlow monitoring reads committed ETL history. It does not schedule pipelines,
execute ETL or stream live changes. Dashboard interactions and the **Refresh
stored data** button fetch current stored results. All examples bind to loopback;
no public deployment, authentication gateway or remote exposure is configured.

## Install and prepare the database

From the repository root in Windows PowerShell:

```powershell
uv sync --frozen --extra dev --extra monitor
# Follow docs/DATABASE.md to start PostgreSQL/PostGIS and apply migrations.
# The privileged migration URL is separate from the monitoring connection.
uv run --frozen --extra dev --extra monitor alembic upgrade head
```

Create a dedicated monitoring login using your database administration tool and
a password kept outside Git. The API additionally sets transactions read-only.
Use a SELECT-only login rather than the ETL writer or a superuser. A minimum grant
template for a newly created `geoflow_monitor` role is:

```sql
GRANT CONNECT ON DATABASE geoflow TO geoflow_monitor;
GRANT USAGE ON SCHEMA geoflow TO geoflow_monitor;
GRANT SELECT (name, configuration, created_at)
  ON geoflow.datasets TO geoflow_monitor;
GRANT SELECT (run_id, dataset_name, status, started_at, finished_at, configuration)
  ON geoflow.pipeline_runs TO geoflow_monitor;
GRANT SELECT ON geoflow.pipeline_metrics TO geoflow_monitor;
GRANT SELECT (run_id, issue_number, row_id, code, severity)
  ON geoflow.validation_issues TO geoflow_monitor;
GRANT SELECT (run_id, row_id, geometry)
  ON geoflow.validated_features TO geoflow_monitor;
```

The role receives no raw snapshot, quarantine, source identifier, feature property
or issue-detail access. Configuration columns are required for safe CRS/format
metadata extraction; arbitrary configuration values are never returned by the API.
Database configuration is still sensitive and the role must remain local/private.

## Start locally

API terminal:

```powershell
$env:GEOFLOW_API_DATABASE_URL = 'postgresql+psycopg://geoflow_monitor:YOUR_URL_ENCODED_PASSWORD@127.0.0.1:5432/geoflow'
# Optional: exact browser origins only. No wildcard CORS.
$env:GEOFLOW_CORS_ORIGINS = 'http://127.0.0.1:8501'
# Optional: enable previews only for explicitly reviewed, nonprivate datasets.
$env:GEOFLOW_PREVIEW_DATASETS = 'sample_points_postgis'
uv run --frozen --extra dev --extra monitor uvicorn geoflow.api.app:app --host 127.0.0.1 --port 8000
```

Dashboard terminal:

```powershell
$env:GEOFLOW_API_URL = 'http://127.0.0.1:8000'
uv run --frozen --extra dev --extra monitor streamlit run src/geoflow/dashboard/app.py --server.address 127.0.0.1 --server.port 8501 --browser.gatherUsageStats false
```

- Dashboard: http://127.0.0.1:8501
- API health: http://127.0.0.1:8000/health
- Interactive API schema: http://127.0.0.1:8000/docs
- OpenAPI JSON: http://127.0.0.1:8000/openapi.json

Dashboard requests are server-to-server, so CORS is not needed for its adapter.
CORS governs direct browser API requests and is not authentication. Stop each
foreground process with Ctrl+C. Do not bind to `0.0.0.0` or publish ports remotely
without reviewing authentication and access controls first.

## Endpoints and bounded responses

All functional endpoints are GET-only. Success responses use `data`; list
responses add `pagination: {limit, offset, total}`. Errors use
`error: {code, message}`. List `limit` is 1–100 and `offset` is 0–10,000.
Unknown resources return 404; invalid parameters return 422. Database failures
return a safe service error without a connection string or SQL exception.

| Endpoint | Purpose |
| --- | --- |
| GET /health | Database/service readiness |
| GET /overview | Aggregate counts across recorded runs |
| GET /datasets | Safe dataset format and CRS metadata |
| GET /runs | Paginated execution history; optional dataset_id filter |
| GET /runs/{run_id} | Execution timestamps, status, duration and counts |
| GET /runs/{run_id}/quality | Pass rate, rule counts and severity counts |
| GET /runs/{run_id}/issues | Paginated issue numbers, codes and severities |
| GET /datasets/{dataset_id}/summary | Metadata and run summary |
| GET /datasets/{dataset_id}/preview | Opt-in bounded simplified GeoJSON |

Preview is disabled by default and only enabled through GEOFLOW_PREVIEW_DATASETS.
It contains geometry only, no source attributes or IDs. Samples are bounded to
100 features; geometry complexity and export bytes are bounded server-side.
The dashboard labels simplification and omitted features. Its view bounds come
from actual returned geometry; no invented geometry or default location is shown.
It uses no external basemap tiles. Preview is an inspection aid, not complete
data or analytical output; enabling it can reveal locations and requires reviewing
the dataset's sensitivity.

The dashboard adapter imposes an 8-second HTTP timeout, 1 MB decoded-response cap,
no redirects, and no credentials embedded in its configured API origin. It never
opens a database connection. Raw API errors are not displayed. Data tables and
exports stay on the selected page rather than downloading every source record.

## Pages and interpretation

- **Overview:** totals across stored runs, published records and unmeasured runs.
  Repeated source snapshots can occur in distinct runs; sums are not unique features.
- **Run History:** paginated timestamps, durations, statuses and record counts.
- **Data Quality:** selected-run pass rate, rule failures and severity distribution.
  Null metrics display as unmeasured, not zero. Pass rate means issue-free records;
  accepted records can contain advisory issues.
- **Dataset Explorer:** safe metadata and an explicitly requested spatial sample.
- **Reports:** JSON run/quality aggregate summary and CSV for the displayed issue
  page only. The filename identifies its offset. Exports exclude source records;
  CSV escapes formula-like values for spreadsheet safety.

Use `uv run --frozen --extra dev --extra monitor pytest -q` for the configured test
suite. Real database tests require a disposable migrated GEOFLOW_TEST_DATABASE_URL.
See VERIFICATION.md for evidence actually collected, rather than assuming browser,
Docker or remote CI checks occurred.

Streamlit integration follows its official [PyDeck chart](https://docs.streamlit.io/develop/api-reference/charts/st.pydeck_chart)
and [download button](https://docs.streamlit.io/develop/api-reference/widgets/st.download_button) documentation.
