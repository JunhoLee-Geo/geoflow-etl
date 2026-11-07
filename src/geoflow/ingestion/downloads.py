"""Bounded public HTTPS downloads; redirects and credential-bearing URLs are rejected.

The adapter buffers at most max_bytes + 1 bytes and never creates a temporary file.
Each request has a socket timeout and a checked per-attempt elapsed-time budget.
Retries apply only to transport failures, HTTP 429 and HTTP 5xx responses; backoff
is capped, and Retry-After is deliberately not allowed to extend that budget.
"""

import hashlib
import math
import re
import time
from datetime import UTC, datetime
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from geoflow.exceptions import IngestionError


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate_public_url(url: str) -> None:
    try:
        parsed = urlsplit(url)
        # Accessing port also checks malformed/out-of-range values.
        _ = parsed.port
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or "#" in url
            or any(ord(char) <= 32 or ord(char) == 127 for char in url)
        ):
            raise ValueError
        forbidden = {
            "key",
            "apikey",
            "token",
            "accesstoken",
            "password",
            "passwd",
            "secret",
            "signature",
            "sig",
            "auth",
            "authorization",
            "credential",
        }
        for key, _value in parse_qsl(parsed.query, keep_blank_values=True):
            normalized = re.sub(r"[^a-z0-9]", "", key.lower())
            if normalized in forbidden or normalized.startswith(("xamz", "xgoog")):
                raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise IngestionError(
            "Remote source must be a public HTTPS URL without credentials"
        ) from None


def _safe_header(value: str | None) -> str | None:
    if value and len(value) <= 1024 and all(32 <= ord(char) < 127 for char in value):
        return value
    return None


def fetch_remote(
    url: str,
    *,
    max_bytes: int,
    timeout_seconds: float,
    retries: int,
    expected_sha256: str | None = None,
) -> tuple[bytes, dict]:
    """Fetch a bounded public file and return bytes plus ingestion provenance.

    ``retries`` counts additional attempts (0..5). Redirects are intentionally
    rejected: configure the publisher's verified final URL instead. No auth
    headers, cookies or credentials are added by this adapter.
    """
    validate_public_url(url)
    if (
        isinstance(max_bytes, bool)
        or not isinstance(max_bytes, int)
        or not 1 <= max_bytes <= 100_000_000
        or isinstance(retries, bool)
        or not isinstance(retries, int)
        or not 0 <= retries <= 5
        or not isinstance(timeout_seconds, (int, float))
        or isinstance(timeout_seconds, bool)
        or not math.isfinite(timeout_seconds)
        or not 0 < timeout_seconds <= 120
    ):
        raise IngestionError("Invalid remote download limits")
    if expected_sha256 is not None and (
        not isinstance(expected_sha256, str)
        or re.fullmatch(r"[a-fA-F0-9]{64}", expected_sha256) is None
    ):
        raise IngestionError("Expected download checksum must be a SHA-256 hex digest")
    opener = build_opener(_NoRedirects())
    request = Request(url, headers={"User-Agent": "GeoFlow-ETL/0.2", "Accept-Encoding": "identity"})
    for attempt in range(1, retries + 2):
        started = time.monotonic()
        try:
            with opener.open(request, timeout=timeout_seconds) as response:
                final_url = response.geturl()
                if final_url != url:
                    raise IngestionError("Remote redirects are not supported; verify the final URL")
                if response.status != 200:
                    raise IngestionError("Remote server did not return a complete file")
                encoding = response.headers.get("Content-Encoding", "identity").lower()
                if encoding != "identity":
                    raise IngestionError("Remote content encoding is unsupported")
                advertised = response.headers.get("Content-Length")
                length = None
                if advertised is not None:
                    if not advertised.isdecimal():
                        raise IngestionError("Remote Content-Length is invalid")
                    length = int(advertised)
                    if length > max_bytes:
                        raise IngestionError("Remote file exceeds the configured byte limit")
                buffer = bytearray()
                read = getattr(response, "read1", response.read)
                while True:
                    if time.monotonic() - started > timeout_seconds:
                        raise TimeoutError
                    chunk = read(min(65536, max_bytes + 1 - len(buffer)))
                    if time.monotonic() - started > timeout_seconds:
                        raise TimeoutError
                    if not chunk:
                        break
                    buffer.extend(chunk)
                    if len(buffer) > max_bytes:
                        raise IngestionError("Remote file exceeds the configured byte limit")
                if length is not None and len(buffer) != length:
                    raise IngestionError("Remote file length does not match Content-Length")
                content = bytes(buffer)
                digest = hashlib.sha256(content).hexdigest()
                if expected_sha256 is not None and digest != expected_sha256.lower():
                    raise IngestionError("Remote file does not match the expected SHA-256 checksum")
                metadata = {
                    "source_url": url,
                    "final_url": final_url,
                    "download_sha256": digest,
                    "download_bytes": len(content),
                    "ingested_at": datetime.now(UTC).isoformat(),
                    "attempts": attempt,
                }
                for name, header in (("etag", "ETag"), ("last_modified", "Last-Modified")):
                    if safe := _safe_header(response.headers.get(header)):
                        metadata[name] = safe
                return content, metadata
        except HTTPError as exc:
            retryable = exc.code == 429 or 500 <= exc.code <= 599
            exc.close()
            if not retryable or attempt > retries:
                raise IngestionError(
                    "Remote HTTP download failed; no source was accepted"
                ) from None
        except (URLError, TimeoutError, OSError):
            if attempt > retries:
                raise IngestionError(
                    "Remote download timed out or failed; retry budget exhausted"
                ) from None
        if attempt <= retries:
            time.sleep(min(0.25 * 2 ** (attempt - 1), 2.0))
    raise IngestionError("Remote download failed")  # Defensive: every attempt returns or raises.
