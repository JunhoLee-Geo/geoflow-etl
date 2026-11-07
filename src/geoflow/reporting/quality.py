"""Publish validation evidence before any quality gate or database operation."""

import csv
import io
from pathlib import Path

from geoflow.reporting.artifacts import write_atomic, write_json
from geoflow.validation.models import ValidationResult


def write_quality_reports(
    directory: Path, result: ValidationResult, *, run_id: str, dataset: str, source: dict
) -> None:
    issues = [issue.model_dump(mode="json") for issue in result.issues]
    write_json(
        directory / "quality.json",
        {
            "run_id": run_id,
            "dataset": dataset,
            "source": source,
            "summary": result.summary.model_dump(mode="json"),
            "issues": issues,
        },
    )
    buffer = io.StringIO(newline="")
    fields = ["run_id", "row_id", "code", "severity", "field", "message"]
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for issue in issues:
        # CSV is a human-facing export; protect spreadsheet formula interpretation.
        # JSON retains the exact unsanitized issue strings.
        row = {**issue, "run_id": run_id}
        for key, value in row.items():
            if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
                row[key] = "'" + value
        writer.writerow(row)
    write_atomic(directory / "issues.csv", buffer.getvalue().encode("utf-8"))
