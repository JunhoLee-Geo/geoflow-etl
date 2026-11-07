"""Container process launcher; passwords are read only from environment."""

import os
import subprocess
import sys

import psycopg
from psycopg import sql
from sqlalchemy.engine import URL


def database_url(monitor=False):
    return URL.create(
        "postgresql+psycopg",
        username="geoflow_monitor" if monitor else os.environ.get("POSTGRES_USER", "geoflow"),
        password=os.environ["GEOFLOW_MONITOR_PASSWORD" if monitor else "POSTGRES_PASSWORD"],
        host="db",
        port=5432,
        database=os.environ.get("POSTGRES_DB", "geoflow"),
    ).render_as_string(hide_password=False)


def main():
    mode, *args = sys.argv[1:]
    if mode == "migrate":
        os.environ["GEOFLOW_DATABASE_URL"] = database_url()
        subprocess.run(["alembic", "upgrade", "head"], check=True)
        url = database_url().replace("postgresql+psycopg://", "postgresql://", 1)
        with psycopg.connect(url) as conn:
            if not conn.execute(
                "SELECT 1 FROM pg_roles WHERE rolname='geoflow_monitor'"
            ).fetchone():
                conn.execute("CREATE ROLE geoflow_monitor LOGIN")
            conn.execute(
                sql.SQL("ALTER ROLE geoflow_monitor PASSWORD {}").format(
                    sql.Literal(os.environ["GEOFLOW_MONITOR_PASSWORD"])
                )
            )
            conn.execute("GRANT USAGE ON SCHEMA geoflow TO geoflow_monitor")
            for statement in [
                "GRANT SELECT (name,configuration,created_at) "
                "ON geoflow.datasets TO geoflow_monitor",
                "GRANT SELECT (run_id,dataset_name,status,started_at,finished_at) "
                "ON geoflow.pipeline_runs TO geoflow_monitor",
                "GRANT SELECT ON geoflow.pipeline_metrics TO geoflow_monitor",
                "GRANT SELECT (run_id,issue_number,row_id,code,severity) "
                "ON geoflow.validation_issues TO geoflow_monitor",
                "GRANT SELECT (run_id,row_id,geometry) "
                "ON geoflow.validated_features TO geoflow_monitor",
            ]:
                conn.execute(statement)
        return
    if mode == "api":
        os.environ["GEOFLOW_API_DATABASE_URL"] = database_url(monitor=True)
        command = ["uvicorn", "geoflow.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
    elif mode == "dashboard":
        command = [
            "streamlit",
            "run",
            "src/geoflow/dashboard/app.py",
            "--server.address",
            "0.0.0.0",
            "--server.port",
            "8501",
            "--server.headless",
            "true",
            "--browser.gatherUsageStats",
            "false",
        ]
    elif mode == "etl":
        if os.environ.get("POSTGRES_PASSWORD"):
            os.environ["GEOFLOW_DATABASE_URL"] = database_url()
        command = ["geoflow", *args]
    else:
        raise ValueError("Unknown container mode")
    os.execvp(command[0], command)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print(
            "Container setup failed; check credentials, database readiness and migrations",
            file=sys.stderr,
        )
        sys.exit(1)
