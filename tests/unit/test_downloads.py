"""Network-free contract tests for bounded public-file ingestion."""

import hashlib
import io
from urllib.error import HTTPError, URLError

import pytest

from geoflow.exceptions import IngestionError
from geoflow.ingestion import downloads

URL = "https://example.test/public/fixture.geojson?f=geojson"


class Response(io.BytesIO):
    def __init__(self, content=b"fixture", *, headers=None, url=URL, status=200):
        super().__init__(content)
        self.headers = headers or {}
        self.url = url
        self.status = status

    def geturl(self):
        return self.url


class Opener:
    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.calls = []

    def open(self, request, *, timeout):
        self.calls.append((request, timeout))
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def setup(monkeypatch, outcomes):
    opener = Opener(outcomes)
    monkeypatch.setattr(downloads, "build_opener", lambda *args: opener)
    monkeypatch.setattr(downloads.time, "sleep", lambda _: None)
    return opener


def fetch(**kwargs):
    settings = dict(max_bytes=100, timeout_seconds=1, retries=2)
    settings.update(kwargs)
    return downloads.fetch_remote(URL, **settings)


def test_success_checksum_provenance_and_close(monkeypatch):
    response = Response(headers={"ETag": "test-v1", "Content-Length": "7"})
    opener = setup(monkeypatch, [response])
    content, metadata = fetch(expected_sha256=hashlib.sha256(b"fixture").hexdigest())
    assert content == b"fixture" and response.closed
    assert metadata["source_url"] == URL
    assert metadata["attempts"] == 1 and metadata["ingested_at"]
    assert opener.calls[0][1] == 1


@pytest.mark.parametrize(
    "error",
    [
        URLError("timeout"),
        TimeoutError(),
        HTTPError(URL, 503, "", {}, None),
        HTTPError(URL, 429, "", {}, None),
    ],
)
def test_retryable_failures(monkeypatch, error):
    opener = setup(monkeypatch, [error, Response()])
    assert fetch()[1]["attempts"] == 2
    assert len(opener.calls) == 2


def test_retry_budget(monkeypatch):
    opener = setup(monkeypatch, [URLError("secret")] * 3)
    with pytest.raises(IngestionError, match="retry budget"):
        fetch()
    assert len(opener.calls) == 3


@pytest.mark.parametrize(
    "response",
    [
        Response(b"x" * 101),
        Response(headers={"Content-Length": "101"}),
        Response(headers={"Content-Length": "8"}),
        Response(headers={"Content-Encoding": "gzip"}),
        Response(url="https://elsewhere.test/data"),
    ],
)
def test_rejected_response_is_closed(monkeypatch, response):
    opener = setup(monkeypatch, [response])
    with pytest.raises(IngestionError):
        fetch()
    assert response.closed and len(opener.calls) == 1


def test_checksum_mismatch(monkeypatch):
    setup(monkeypatch, [Response()])
    with pytest.raises(IngestionError, match="checksum"):
        fetch(expected_sha256="0" * 64)


@pytest.mark.parametrize("code", [301, 302, 400, 403, 404])
def test_no_retry_for_client_error_or_redirect(monkeypatch, code):
    opener = setup(monkeypatch, [HTTPError(URL, code, "", {}, None)])
    with pytest.raises(IngestionError):
        fetch()
    assert len(opener.calls) == 1


@pytest.mark.parametrize(
    "url",
    [
        "http://example.test/data",
        "file:///data",
        "https://user:secret@example.test/data",
        "https://example.test/data?api_key=secret",
        "https://example.test/data#secret",
    ],
)
def test_credential_or_unsafe_urls_rejected(url):
    with pytest.raises(IngestionError):
        downloads.fetch_remote(url, max_bytes=100, timeout_seconds=1, retries=0)
