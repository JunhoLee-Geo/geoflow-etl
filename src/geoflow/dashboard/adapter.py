"""Bounded HTTP reads and safe report serialization for the monitoring UI."""

import csv
import io
import json
import re
from typing import Any
from urllib.parse import urlsplit

import httpx

MAX_RESPONSE_BYTES = 1_000_000
_PATH = re.compile(
    r"/(?:health|overview|datasets|runs|runs/[A-Za-z0-9_-]{1,128}(?:/quality|/issues)?|datasets/[a-z][a-z0-9_]{0,47}/(?:summary|preview))$"
)


class MonitoringError(RuntimeError):
    """Safe UI error; never exposes server internals or credentials."""


class MonitorClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8000", *, transport=None):
        parsed = urlsplit(base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise MonitoringError("Configure a valid API origin without credentials or a path.")
        self.base_url = base_url.rstrip("/")
        self.transport = transport

    def get(self, path: str, **params: Any) -> dict:
        if not _PATH.fullmatch(path):
            raise MonitoringError("Unsupported monitoring endpoint.")
        if "limit" in params and not 1 <= params["limit"] <= 100:
            raise MonitoringError("Page size must be between 1 and 100.")
        if "offset" in params and not 0 <= params["offset"] <= 10_000:
            raise MonitoringError("Page offset exceeds the monitoring limit.")
        try:
            with httpx.Client(
                timeout=8, transport=self.transport, follow_redirects=False, trust_env=False
            ) as client:
                with client.stream("GET", self.base_url + path, params=params) as response:
                    if response.status_code == 403:
                        raise MonitoringError("Spatial preview is disabled for this dataset.")
                    if response.status_code == 404:
                        raise MonitoringError("The selected dataset or run was not found.")
                    if response.status_code != 200:
                        raise MonitoringError(
                            "The monitoring API is unavailable or rejected the request."
                        )
                    content = bytearray()
                    for chunk in response.iter_bytes():
                        if len(content) + len(chunk) > MAX_RESPONSE_BYTES:
                            raise MonitoringError("API response exceeds the dashboard size limit.")
                        content.extend(chunk)
                    payload = json.loads(content)
            if not isinstance(payload, dict) or "data" not in payload:
                raise MonitoringError("The API returned an unsupported response.")
            return payload
        except (httpx.HTTPError, ValueError, TypeError):
            raise MonitoringError(
                "Unable to read monitoring data. Check the local API service."
            ) from None


def json_report(run: dict, quality: dict) -> str:
    return json.dumps(
        {"run": run, "quality": quality}, indent=2, ensure_ascii=False, allow_nan=False
    )


def issue_csv(rows: list[dict]) -> str:
    """Export only the current issue page, never embedded source/detail fields."""
    columns = ["issue_number", "code", "severity"]
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=columns)
    writer.writeheader()
    for row in rows:
        safe = {key: row.get(key) for key in columns}
        for key, value in safe.items():
            if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
                safe[key] = "'" + value
        writer.writerow(safe)
    return output.getvalue()
