# Recruiter-review verification — 2026-11-06

- Final complete suite: **176 passed in 12.67s**, zero skipped, including native
  PostgreSQL17.11/PostGIS3.6 integration tests. Three warnings: upstream Starlette
  TestClient deprecation plus two temporary capture-tool metadata warnings.
- Ruff lint/format passed (59 files); mypy passed (32 application modules).
- Locked installation, README validate-config and synthetic run commands executed.
  Package wheel/source build passed. README/portfolio relative links checked.
- Genuine screenshot evidence captured from running Streamlit/FastAPI/PostGIS in
  the connected browser: Overview, Data Quality and scrolled rule/severity charts.
  No image values edited. SHA-256 image manifest: docs/evidence/screenshots.json.
- Actual first publication report copied to docs/evidence/synthetic-run.json:
  run a9d5da838497f5b087d6b7c1, input10, accepted1, quarantined9, issues14,
  issue-free10%, duration0.422413seconds. Source SHA256 matches bundled fixture.
  Single synthetic execution, not a performance benchmark. Timing scope in README.
- Browser launch from the shell was blocked; connected-browser capture succeeded.
  A temporary capture dependency caused Windows file-lock/metadata cleanup issues;
  it was restored/uninstalled and uv sync completed. No capture dependency was
  added to pyproject/uv.lock. Database stopped after integration tests.

No code functionality was changed for portfolio presentation. Docker runtime,
remote GitHub Actions, public-source licensing/integration and production-scale
performance remain unverified. Scheduled execution remains disabled. No push,
publication, deployment or paid provisioning occurred.

---

## Earlier implementation verification

# Verification — version 0.6.0, 2026-11-06

- `uv lock` and `uv sync --frozen --extra dev --extra monitor`: passed.
- `ruff check .`: passed; `ruff format --check .`: 57 files formatted.
- `mypy`: success, no issues in 32 application source files. Checks untyped
  function bodies; missing third-party stubs allowed. Not strict-mode checking.
- `python -m pytest -q --tb=short`: **176 passed in 19.60 seconds**, no skips.
  Real native PostgreSQL17.11/PostGIS3.6 used for integration tests, including
  transaction rollback, idempotency, failure audit and read-only API enforcement.
- Synthetic quality input executed twice: 10 input = 1 accepted + 9 quarantined;
  identical run ID and accepted GeoJSON hash across repeats.
- SIGTERM CLI exit130, restored signal handlers, failed-report/source retention
  and lock cleanup verified. Existing database rollback tests passed.
- `compileall -q src migrations docker`: passed.
- `uv build --offline`: built 0.6.0 wheel and source distribution.
- Workflow/Compose YAML safety tests passed: no schedules, read-only workflow
  permissions, narrow report uploads, seven-day retention, loopback ports and
  no writer credentials in API/dashboard configuration.

Type checking initially found seven errors; annotations and missing-metrics
handling were corrected. One upstream Starlette TestClient httpx deprecation
warning remains, with no test failures. Database was stopped after verification.

Docker is unavailable: Dockerfile/image build, Compose runtime/health checks and
container role provisioning were NOT executed. YAML parsing/safety tests are not
Docker Compose runtime validation. CI/manual workflows were written but neither
published nor executed on GitHub. No weekly schedule is enabled. Container/action
version tags are not immutable digests; docs describe this reproducibility limit.
No deployment, recurring job or paid service was provisioned.

See README.md, docs/AUTOMATION.md and docs/CONTAINERS.md for instructions.

---

## Historical verification

# Verification — version 0.5.0, 2026-11-06

- `uv lock` / `uv sync --frozen --extra dev --extra monitor`: passed.
- `ruff check .`: passed; `ruff format --check .`: 53 files formatted.
- `python -m compileall -q src migrations`: passed; no static type-check claim.
- `uv build --offline`: built version 0.5.0 wheel and source distribution.
- `python -m pytest -q --tb=short`: **172 passed in 10.63 seconds**, zero skips.
  Includes real native PostGIS storage/API tests, bounded pagination and error
  tests, export/privacy checks, and two Streamlit AppTests covering five empty
  page states plus service-error rendering.
- One third-party Starlette warning remains: its TestClient deprecates httpx in
  favour of httpx2. Tests pass; no suppression or unrequested package was added.

Actual local HTTP smoke checks used temporary loopback ports 18000/18501:
API /docs returned 200, API /health without DB configuration correctly returned
503, Streamlit /_stcore/health and / returned 200. Real configured DB readiness,
queries and transaction write rejection were tested with FastAPI TestClient and
native PostgreSQL17.11/PostGIS3.6. Preview returned the actual synthetic geometry.
This is not a browser visual audit. No Docker or remote CI execution is claimed.
All smoke services and the disposable database were stopped after verification.

An initial unit run hit sandbox TEMP permissions; process-local TEMP/TMP pointing
to the writable workspace resolved it. Child agents could not create Windows
asyncio sockets, so runtime tests were rerun successfully in the parent with a
network grant. Final results above reflect the complete current implementation.

Monitoring extras remain optional for ETL-only installations. Install monitor
extras to run the entire test suite. API uses a separate read-only-role URL and
read-only transactions, no mutation endpoints, explicit CORS origins, and opt-in
bounded geometry-only previews. Dashboard refreshes stored snapshots, not live
streaming telemetry. See docs/MONITORING.md for exact startup commands and URLs.
No public deployment or database exposure occurred.

---

## Historical verification

# Verification — version 0.4.0, 2026-11-05 (local time)

- `uv lock` and `uv sync --frozen --extra dev`: passed; installed Alembic 1.20.1.
- `alembic upgrade head`: applied 0001_run_storage to the existing disposable
  native PostgreSQL 17.11 / PostGIS 3.6 database. Repeated upgrade succeeded.
- `python -m pytest -q --tb=short`: **128 passed in 17.80 seconds**, zero skipped
  (116 unit/file/CLI tests and 12 real PostGIS integration tests).
- `ruff check .`: passed; `ruff format --check .`: 42 files formatted.
- `python -m compileall -q src migrations`: passed; no mypy/pyright run claimed.
- `uv build --offline`: built 0.4.0 wheel and source distribution after final fix.

New database coverage: idempotent identical publication, preserved snapshot/run
history, exact source bytes/checksum, GiST and SRID, accepted/quarantine/issues
counts, quality failure audit without publication, failed-to-success retry,
identity conflict rejection, mixed Point/LineString and typed source IDs,
pre-validation failures with null counts, and pipeline-to-database repeat runs.
A real CHECK constraint failure during metrics insertion proved all earlier
inserts in that transaction roll back. Temporary test constraints/rows were
removed, and the disposable PostgreSQL server was stopped after testing.

The first full suite found a Windows path-length failure in atomic report temp
filenames (127 passed, 1 failed). Shorter same-directory temporary names fixed it;
the final 128-test suite and rebuilt artifacts include that fix.

Docker executable is unavailable: Compose was configured but NOT executed.
The configured Docker PostgreSQL16/PostGIS3.4 image was not tested locally;
actual integration evidence is native PostgreSQL17.11/PostGIS3.6. Remote CI was
updated to run Alembic but was not published or executed. No deployment occurred.
Storage is explicitly 2D EPSG:26917; raw source snapshots preserve original data.
The local reports and database commit are not a distributed atomic transaction.
See docs/DATABASE.md for setup, migration and operational limits.

---

## Historical verification

# Verification — version 0.3.0, 2026-11-05 (local time)

- `uv lock --offline` and `uv sync --offline --frozen --extra dev`: passed.
- `ruff check .`: passed; `ruff format --check .`: 36 files formatted.
- `python -m compileall -q src`: passed. No static type checker is configured.
- `python -m pytest -q --tb=short`: **120 passed in 14.67 seconds**, no skips.
  Includes 116 unit/file/CLI tests and four real native PostGIS integration tests.
- `uv build --offline`: built 0.3.0 wheel and source distribution.
- `python -m geoflow.cli validate-config configs/quality.yml`: passed.
- `python -m geoflow.cli run configs/quality.yml --dataset synthetic_quality`: passed.
  Synthetic input 10 = accepted 1 + quarantined 9. Fourteen issues; issue-free
  pass rate 0.1. These are deliberate test outcomes, not real dataset metrics.
- JSON quality report, CSV issue report and raw-record quarantine inspected.
  Tests assert exact rule-code sets, reconciliation, source preservation,
  repeat determinism, advisory policies and FATAL prevention of database loading.

The initial combined test attempt had four sandbox TCP connection errors, while
116 tests passed. After a turn-scoped network grant, all 120 passed against the
existing disposable PostgreSQL 17.11 / PostGIS 3.6 instance. The server was stopped.
Initial new-test assertions were corrected to include null geometry's bounds and
parser issues; final exact-code assertions passed.

No geometry repair policy is implemented; automatic repair never occurs.
Unknown CRS is rejected, never guessed. Malformed whole files fail separately
without fabricated record reconciliation. No Docker, remote CI, public source
integration or deployment was executed. Native Python typing is present but no
mypy/pyright success is claimed. Previous verification below is historical.

---

# Verification — version 0.2.0, 2026-11-05 (local time)

## Fresh verification results

- `uv lock --offline`: refreshed version 0.2.0 lockfile.
- `uv sync --offline --frozen --extra dev`: passed.
- `ruff check .`: passed; `ruff format --check .`: 32 files formatted.
- `python -m compileall -q src`: passed (syntax check, not static type checking).
- `python -m pytest -q --tb=short` with disposable native PostGIS:
  **100 passed in 8.21 seconds, zero skipped** (96 unit/file/CLI tests + 4 database tests).
- `uv build --offline`: wheel and source distribution built for 0.2.0.
- `python -m geoflow.cli validate-config configs/normalized.yml`: valid.
- CLI run `configs/normalized.yml --dataset synthetic_geopackage`: 2 accepted, 0 rejected.
- CLI run `configs/sample.yml --dataset sample_points`: 3 accepted, 3 rejected.
- CLI run `configs/sample.yml --dataset sample_geojson`: 1 accepted, 1 rejected.

All example records are synthetic. The GeoPackage example records field copying,
string trimming, Point-to-MultiPoint promotion and EPSG:4326 → EPSG:26917
transformation; exported GeoJSON remains EPSG:4326.

The first build attempt hit Windows sandbox temporary executable permissions;
setting process-local TEMP/TMP to the writable workspace resolved it. The first
database suite had four connection errors because sandbox TCP was blocked.
After granting network access, the complete suite passed against native
PostgreSQL 17.11 / PostGIS 3.6 on loopback port 55439. The disposable server was
stopped afterwards. No Docker runtime or remote CI was exercised.

No static type checker is configured. Python annotations, configuration validation,
Ruff, compilation and runtime tests were checked; this is not a mypy/pyright pass.
HTTPS download tests use mocked responses and cover retries, size limits,
checksums, redirects and unsafe URLs. No live official data download is claimed.
See docs/SOURCES.md for pending dataset-specific licence/schema/CRS/update/download
verification. GeoPackage supports a documented conservative subset, not every
GeoPackage extension. No deployment or publication occurred.

---

## Historical version 0.1 verification

# Verification — 2026-11-05

## Actual results

| Check | Result |
| --- | --- |
| Python environment | Python 3.12.14, isolated .venv |
| Locked installation | uv lock and uv sync --frozen --extra dev completed |
| Ruff lint | Passed |
| Ruff format | Passed |
| Unit / file end-to-end / CLI tests | 50 passed |
| Real PostGIS integration tests | 4 passed |
| Combined pytest suite with configured DB | **54 passed, zero skipped** |
| CSV CLI example | 6 records: 3 accepted + 3 quarantined |
| GeoJSON CLI example | 2 records: 1 accepted + 1 quarantined |
| Package build | Wheel and source distribution built |
| Docker Compose | Not run: Docker executable unavailable |
| GitHub Actions | Defined, not published or executed remotely |

## Database evidence

An isolated native PostgreSQL 17.11 cluster was initialized under the workspace work directory, not the repository, with a randomly generated password kept outside deliverables. It listened only on 127.0.0.1:55439. The explicit migration created PostGIS 3.6 and the GeoFlow metadata schema. The real database tests checked idempotent row replacement, GiST index/SRID, rollback of rows and metadata, explicit empty adapter snapshots, and CRS-change protection. The combined suite finished `54 passed in 13.87s`.

The normal CLI PostGIS example was also run twice against this disposable database; see the CLI evidence below. The server was stopped after each successful test session. No existing user database was modified. Early attempts encountered sandbox network denial and process-lifetime limits; final tests were run with local network permission and startup/test/shutdown in one shell session.

Docker Compose's PostgreSQL16/PostGIS3.4 image is separate from the native tested PostgreSQL17/PostGIS3.6 combination. Its runtime behavior and the GitHub-hosted Windows/Linux jobs remain unverified until those environments are run.

## Commands executed

```text
uv venv .venv --python <available Python 3.12.14>
uv pip install --python .venv/Scripts/python.exe -e .[dev]
uv lock --python .venv/Scripts/python.exe
uv sync --frozen --extra dev --python .venv/Scripts/python.exe
.venv/Scripts/ruff.exe check .
.venv/Scripts/ruff.exe format --check .
.venv/Scripts/python.exe -m pytest -q --tb=short
.venv/Scripts/python.exe -m geoflow.cli validate-config configs/sample.yml
.venv/Scripts/python.exe -m geoflow.cli run configs/sample.yml --dataset sample_points
.venv/Scripts/python.exe -m geoflow.cli run configs/sample.yml --dataset sample_geojson
.venv/Scripts/python.exe -m geoflow.cli run configs/postgis.yml --dataset sample_points_postgis
uv build
```

The full test command used GEOFLOW_TEST_DATABASE_URL pointed only at the disposable database. Without that variable, the four database tests explicitly skip; no mock result is reported as a real PostGIS test. Workspace TEMP/TMP and UV_CACHE_DIR overrides were necessary in this restricted Windows tool environment. Dependency download required network permission; subsequent lock/sync operations also succeeded using the local cache offline.

## Defects found and fixed during development

- Invalid Shapely geometry could escape per-row quarantine: geometry exceptions are now captured per record.
- Shapely implicitly closed invalid polygon rings: raw Polygon/MultiPolygon ring closure is now checked before construction.
- Duplicate JSON members could overwrite values during parsing: duplicate keys now fail document ingestion with the original snapshot retained.
- Fully rejected input could otherwise be an empty database replacement: the CLI explicitly fails before loading.
- Credentials in underlying database exception text are not copied into logs or reports.

## Limits

This is an initial bounded, in-memory ETL implementation, not a deployed service. No scheduler, web dashboard, real source connector, domain-specific quality rules or streaming execution is claimed. Cross-system filesystem/database atomicity is not guaranteed; deterministic reruns reconcile partial completion. Source licensing and provenance fields are supplied by the dataset owner. Tests use synthetic geometry only. No performance claims or real geospatial analysis results are fabricated.

The separate Git repository is initialized locally on main with no remote and no commits. The existing portfolio website was not modified. No deployment or publication occurred.

## CLI database evidence

The PostGIS CLI example completed twice with the same run ID. A direct SQL query after the second run returned **3 rows, SRID26917**, confirming no duplicate accumulation. Both runs reported 3 accepted and 3 quarantined source records. The temporary server then shut down cleanly.
