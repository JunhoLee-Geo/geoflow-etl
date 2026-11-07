import hashlib
import json
import logging
import os
import platform
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from time import perf_counter
from typing import Any

from geoflow import __version__
from geoflow.config import Dataset
from geoflow.exceptions import GeoFlowError, IngestionError, QualityError, RunLockedError
from geoflow.ingestion.downloads import fetch_remote
from geoflow.ingestion.readers import ingest
from geoflow.reporting.artifacts import write_atomic, write_json
from geoflow.reporting.quality import write_quality_reports
from geoflow.transformation.normalization import normalize_records
from geoflow.transformation.spatial import transform
from geoflow.validation.quality import validate

logger = logging.getLogger("geoflow")


def run_dataset(config: Dataset, output_dir: Path) -> Path:
    """Content-addressed runs; report.json is the final completion marker.

    A retry overwrites deterministic artifacts, never appends records. Database
    replacement and report publication are separate commits; retries reconcile.
    """
    started = perf_counter()
    if config.url is not None:
        content, provenance = fetch_remote(
            config.url,
            max_bytes=config.max_source_bytes,
            timeout_seconds=config.download_timeout_seconds,
            retries=config.download_retries,
            expected_sha256=config.expected_sha256,
        )
    else:
        assert config.path is not None
        if config.format == "gpkg" and any(
            Path(str(config.path) + suffix).exists() for suffix in ("-wal", "-journal")
        ):
            raise IngestionError("Export a closed standalone GeoPackage before ingestion")
        try:
            with config.path.open("rb") as stream:
                content = stream.read(config.max_source_bytes + 1)
        except OSError as exc:
            raise IngestionError("Source file is unavailable") from exc
        provenance = {
            "source_url": None,
            "source_name": config.path.name if config.path else f"download.{config.format}",
            "ingested_at": datetime.now(UTC).isoformat(),
        }
    if len(content) > config.max_source_bytes:
        raise IngestionError("Source exceeds configured byte limit; original file is untouched")
    if config.expected_sha256 and hashlib.sha256(content).hexdigest() != config.expected_sha256:
        raise IngestionError("Source checksum does not match configuration")
    digest = hashlib.sha256(content).hexdigest()
    settings = config.model_dump(mode="json")
    # Relocation of identical inputs does not change analytical run identity.
    settings.pop("path")
    fingerprint = (
        json.dumps(settings, sort_keys=True).encode() + digest.encode() + __version__.encode()
    )
    run_id = hashlib.sha256(fingerprint).hexdigest()[:24]
    directory = output_dir / config.name / run_id
    directory.mkdir(parents=True, exist_ok=True)
    if config.path is not None and config.path.resolve().is_relative_to(directory.resolve()):
        raise IngestionError("Source must not be inside its output run directory")
    lock = directory / ".lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise RunLockedError("Run is locked; check the owning process before recovery") from exc
    os.close(fd)
    report: dict[str, Any] = {
        "run_id": run_id,
        "dataset": config.name,
        "version": __version__,
        "started_at": datetime.now(UTC).isoformat(),
        "status": "running",
        "source_sha256": digest,
        "source_bytes": len(content),
        "source_name": config.path.name if config.path else f"download.{config.format}",
        "config": settings,
        "artifact_crs": "EPSG:4326",
        "source_crs": config.source_crs,
        "target_crs": config.target_crs,
        "provenance": provenance,
        "transformations": [],
        "warnings": [],
        "environment": {
            "python": platform.python_version(),
            "packages": {
                name: version(name)
                for name in (
                    "geopandas",
                    "pandas",
                    "shapely",
                    "pyproj",
                    "pandera",
                    "pydantic",
                    "sqlalchemy",
                    "psycopg",
                )
            },
        },
        "database_committed": False,
    }
    result = None
    storage_attempted = False
    try:
        write_json(directory / "report.json", report)
        snapshot = directory / f"source.{config.format}"
        if snapshot.exists():
            if hashlib.sha256(snapshot.read_bytes()).hexdigest() != digest:
                raise IngestionError("Existing raw snapshot differs; refusing to overwrite it")
        else:
            with snapshot.open("xb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        logger.info("run_started", extra={"fields": {"run_id": run_id, "dataset": config.name}})
        records = ingest(snapshot.read_bytes(), config)
        records, history, warnings = normalize_records(records, config)
        report["transformations"] = history
        report["warnings"] = warnings
        result = validate(records, config)
        report.update(
            total=len(records),
            accepted=len(result.accepted),
            rejected=len(result.rejected),
            rule_counts=result.counts,
            quality_summary=result.summary.model_dump(mode="json"),
        )
        quarantine = "".join(
            json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n" for row in result.rejected
        )
        write_atomic(directory / "quarantine.jsonl", quarantine.encode())
        write_quality_reports(
            directory,
            result,
            run_id=run_id,
            dataset=config.name,
            source={
                **provenance,
                "sha256": digest,
                "source_crs": config.source_crs,
                "target_crs": config.target_crs,
            },
        )
        if result.summary.has_fatal:
            raise QualityError("Fatal validation issue; source and quality evidence retained")
        transformed = transform(result.accepted, config.target_crs)
        report["transformations"].extend(
            [
                {
                    "operation": "reproject",
                    "source_crs": config.source_crs,
                    "target_crs": config.target_crs,
                },
                {
                    "operation": "geojson_export",
                    "source_crs": config.target_crs,
                    "target_crs": "EPSG:4326",
                },
            ]
        )
        exported = transform(transformed, "EPSG:4326")
        artifact_doc = json.loads(exported.to_json(drop_id=True, na="null"))
        for feature in artifact_doc["features"]:
            feature["id"] = feature["properties"]["__feature_id"]
        artifact = json.dumps(artifact_doc, ensure_ascii=False, allow_nan=False)
        write_atomic(directory / "accepted.geojson", artifact.encode())
        if len(result.rejected) / len(records) > config.max_invalid_fraction:
            raise QualityError("Invalid record fraction exceeds the configured quality gate")
        if result.accepted.empty:
            raise QualityError("No valid records; existing database snapshot will not be cleared")
        if config.sink == "postgis":
            from geoflow.database.repository import publish_run

            url = os.environ.get("GEOFLOW_DATABASE_URL")
            if not url:
                raise GeoFlowError("GEOFLOW_DATABASE_URL is required for the PostGIS sink")
            storage_attempted = True
            report["loaded"] = publish_run(
                database_url=url,
                config=config,
                result=result,
                run_id=run_id,
                source_bytes=content,
                provenance=provenance,
                started_at=datetime.fromisoformat(report["started_at"]),
                duration_seconds=perf_counter() - started,
            )
            report["database_committed"] = True
        report["status"] = "succeeded"
    except Exception as exc:
        report["status"] = "quality_failed" if isinstance(exc, QualityError) else "failed"
        report["error_type"] = type(exc).__name__
        # Audit failed processing separately, without publishing accepted features.
        # A failed publication transaction is not retried as a different outcome.
        if config.sink == "postgis" and not storage_attempted:
            url = os.environ.get("GEOFLOW_DATABASE_URL")
            if url:
                try:
                    from geoflow.database.repository import publish_run

                    publish_run(
                        database_url=url,
                        config=config,
                        result=result,
                        run_id=run_id,
                        source_bytes=content,
                        provenance=provenance,
                        started_at=datetime.fromisoformat(report["started_at"]),
                        duration_seconds=perf_counter() - started,
                        status=report["status"],
                        error_type=type(exc).__name__,
                    )
                    report["database_audit_committed"] = True
                except Exception:
                    report["database_audit_committed"] = False

        logger.error(
            "run_failed", extra={"fields": {"run_id": run_id, "error_type": type(exc).__name__}}
        )
        if isinstance(exc, GeoFlowError):
            raise
        raise GeoFlowError("Pipeline failed; inspect the run report and retained source") from exc
    finally:
        report["finished_at"] = datetime.now(UTC).isoformat()
        report["duration_seconds"] = round(perf_counter() - started, 6)
        try:
            report["artifacts"] = {
                p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in directory.iterdir()
                if p.is_file()
                and p.name
                in {
                    "accepted.geojson",
                    "quarantine.jsonl",
                    "quality.json",
                    "issues.csv",
                    f"source.{config.format}",
                }
            }
            write_json(directory / "report.json", report)
        finally:
            lock.unlink(missing_ok=True)
    logger.info(
        "run_completed",
        extra={
            "fields": {
                "run_id": run_id,
                "accepted": report["accepted"],
                "rejected": report["rejected"],
            }
        },
    )
    return directory / "report.json"
