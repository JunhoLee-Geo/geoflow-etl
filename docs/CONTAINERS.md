# Local containers

Requires Docker Desktop with Linux containers and Compose v2. Docker is not
installed on the development host: these files were statically checked, not built
or executed there. Python dependencies are frozen by uv.lock. Python3.12.12 and
PostGIS16/3.4 tags are versioned but not immutable digests; review/pin digests for
fully reproducible infrastructure. The image uses a non-root application user.

From the repository root in Windows PowerShell:

```powershell
Copy-Item .env.example .env
# Edit .env: set distinct POSTGRES_PASSWORD and GEOFLOW_MONITOR_PASSWORD.
docker compose config --quiet
docker compose build
docker compose up -d --wait
docker compose ps
```

Do not print resolved `docker compose config` output into logs; it contains env
passwords. The migration job runs Alembic, creates/updates a SELECT-only monitor
role and exits. API startup waits for successful migrations; dashboard startup
waits for API health. ETL is an explicit profile, never a scheduled service.
Container URLs are assembled safely from password variables; no manual password
URL escaping is needed. Do not reuse an existing privileged geoflow_monitor role.

- API: http://127.0.0.1:8000/docs
- Dashboard: http://127.0.0.1:8501
- PostgreSQL: 127.0.0.1:5432 (or POSTGRES_PORT)

All host ports bind to loopback. 0.0.0.0 inside the app containers is necessary for
container networking; it does not widen the host binding. Never publish these
ports remotely without approval and access controls. The API does not receive the
writer password; the dashboard receives no database password.

```powershell
# File-only synthetic execution; named volume retains reports.
docker compose --profile etl run --rm etl etl run configs/sample.yml --dataset sample_points
# Validated records + audit history in PostGIS:
docker compose --profile etl run --rm etl etl run configs/postgis.yml --dataset sample_points_postgis
# Inspect a one-off run without auto-removing the stopped container, then copy reports:
docker compose --profile etl run --name geoflow-report-run etl etl run configs/quality.yml --dataset synthetic_quality
docker cp geoflow-report-run:/app/reports ./container-reports
docker rm geoflow-report-run
# Upgrade an existing stack after rebuilding:
docker compose run --rm migrate
docker compose up -d --wait
# Stop services; database/reports volumes remain:
docker compose down
```

`down -v` destroys persisted database and reports: it is not a routine shutdown
command. PostgreSQL initializes its password only on a fresh volume; changing .env
alone does not rotate an existing database account. Monitor password is updated
by the explicit migration job. Migration/container logs sanitize setup exceptions.
Long-running app processes are exec-launched with init and a30-second stop grace
period. Jobs use process completion/exit status rather than a daemon healthcheck.
No aggressive restart policy or automatic pipeline retries are configured.

Source image context allowlists application code, migrations, configs and tiny
synthetic fixtures; it excludes .env, raw downloads, reports, virtual environments
and Git metadata. For real data, review sensitivity and mount an explicit local
file path; do not add large raw datasets to the image or Git.
