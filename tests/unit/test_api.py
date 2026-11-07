import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from geoflow.api.app import create_app
from geoflow.api.store import MissingResource, MonitoringStore, bounded_preview

RUN = {
    "run_id": "abc",
    "dataset_id": "synthetic",
    "status": "succeeded",
    "started_at": "2026-11-06T00:00:00Z",
    "finished_at": "2026-11-06T00:00:01Z",
    "duration_seconds": 1.0,
    "input_records": 10,
    "accepted_records": 1,
    "quarantined_records": 9,
    "published_records": 1,
}
DATASET = {
    "dataset_id": "synthetic",
    "created_at": "2026-11-06T00:00:00Z",
    "source_crs": "EPSG:4326",
    "target_crs": "EPSG:26917",
    "geometry_types": ["Point"],
    "source_format": "geojson",
}


class FakeStore:
    def health(self):
        return {"status": "ready", "mode": "read_only"}

    def overview(self):
        return dict(
            total_runs=0,
            successful_runs=0,
            failed_runs=0,
            input_records=0,
            accepted_records=0,
            quarantined_records=0,
            published_records=0,
            unmeasured_runs=0,
        )

    def datasets(self, limit, offset):
        return ([DATASET] if offset == 0 else []), 1

    def runs(self, limit, offset, dataset_id):
        return ([RUN] if offset == 0 and dataset_id in {None, "synthetic"} else []), 1

    def run(self, run_id):
        if run_id != "abc":
            raise MissingResource
        return {**RUN, "configuration": {"password": "PRIVATE"}}

    def quality(self, run_id):
        self.run(run_id)
        return {
            "run_id": run_id,
            "pass_rate": 0.1,
            "input_records": 10,
            "accepted_records": 1,
            "quarantined_records": 9,
            "rule_counts": {"duplicate_id": 2},
            "severity_counts": {"ERROR": 2},
        }

    def issues(self, run_id, limit, offset):
        self.run(run_id)
        return (
            [
                {
                    "issue_number": 1,
                    "code": "duplicate_id",
                    "severity": "ERROR",
                    "details": {"message": "PRIVATE"},
                    "row_id": 123,
                }
            ]
            if offset == 0
            else []
        ), 1

    def summary(self, dataset_id):
        return {
            **DATASET,
            "total_runs": 1,
            "successful_runs": 1,
            "failed_runs": 0,
            "latest_run_id": "abc",
        }

    def preview(self, dataset_id, limit):
        return bounded_preview(
            dataset_id, "abc", ['{"type":"Point","coordinates":[-80,43]}'], limit
        )


@pytest.fixture
def client():
    with TestClient(create_app(FakeStore())) as client:
        yield client


def test_all_read_endpoints_and_filtered_models(client):
    paths = [
        "/health",
        "/overview",
        "/datasets",
        "/runs",
        "/runs/abc",
        "/runs/abc/quality",
        "/runs/abc/issues",
        "/datasets/synthetic/summary",
    ]
    for path in paths:
        response = client.get(path)
        assert response.status_code == 200, response.text
        assert "data" in response.json()
        assert "PRIVATE" not in response.text
    assert "row_id" not in client.get("/runs/abc/issues").text


@pytest.mark.parametrize(
    "path",
    [
        "/runs?limit=101",
        "/datasets?limit=0",
        "/runs?offset=-1",
        "/runs?offset=10001",
        "/runs/abc/issues?limit=1000",
        "/runs?dataset_id=bad%27",
        "/runs/bad%27",
        "/datasets/synthetic/preview?limit=101",
    ],
)
def test_input_bounds_and_untrusted_values_not_reflected(client, path):
    response = client.get(path)
    assert response.status_code == 422
    assert response.json() == {
        "error": {"code": "invalid_request", "message": "Request parameters are invalid"}
    }


def test_pagination_empty_page_missing_resource_and_read_only(client):
    response = client.get("/runs?limit=1&offset=1").json()
    assert response == {"data": [], "pagination": {"limit": 1, "offset": 1, "total": 1}}
    assert client.get("/runs/missing").status_code == 404
    assert client.get("/runs/missing/issues").status_code == 404
    assert client.get("/overview").json()["data"]["total_runs"] == 0
    assert client.post("/runs", json={"execute": True}).status_code == 405
    assert client.post("/run").status_code == 404


def test_no_database_url_is_unavailable_without_leaking_credentials(monkeypatch):
    monkeypatch.delenv("GEOFLOW_API_DATABASE_URL", raising=False)
    monkeypatch.setenv("GEOFLOW_DATABASE_URL", "postgresql://writer:PRIVATE@localhost/db")
    with TestClient(create_app()) as client:
        response = client.get("/health")
        assert response.status_code == 503
        assert "PRIVATE" not in response.text


def test_database_errors_are_sanitized():
    class Broken(FakeStore):
        def runs(self, *args):
            raise OperationalError("PRIVATE SQL", {}, RuntimeError("password=PRIVATE"))

    with TestClient(create_app(Broken())) as client:
        response = client.get("/runs")
        assert response.status_code == 503
        assert "PRIVATE" not in response.text


def test_cors_restricted_to_configured_origins(monkeypatch):
    monkeypatch.setenv("GEOFLOW_CORS_ORIGINS", "http://localhost:8501")
    with TestClient(create_app(FakeStore())) as client:
        assert (
            client.get("/health", headers={"Origin": "http://localhost:8501"}).headers[
                "access-control-allow-origin"
            ]
            == "http://localhost:8501"
        )
        assert (
            "access-control-allow-origin"
            not in client.get("/health", headers={"Origin": "https://untrusted.test"}).headers
        )
    monkeypatch.setenv("GEOFLOW_CORS_ORIGINS", "*")
    with pytest.raises(ValueError):
        create_app(FakeStore())


def test_preview_opt_in_and_size_budget(client):
    assert client.get("/datasets/synthetic/preview").status_code == 403
    with TestClient(create_app(FakeStore(), preview_datasets={"synthetic"})) as enabled:
        data = enabled.get("/datasets/synthetic/preview?limit=1").json()["data"]
        assert data["returned_features"] == 1
        assert data["geojson"]["features"][0]["properties"] == {}
    geometries = [
        json.dumps({"type": "LineString", "coordinates": [[-80.12345, 43.12345]] * 1000})
    ] * 100
    data = bounded_preview("synthetic", "abc", geometries, 100)
    assert len(json.dumps(data).encode()) < 100000
    assert data["omitted_features"] > 0
    assert data["sampled_features"] == data["returned_features"] + data["omitted_features"]


def test_store_requires_configured_engine():
    from geoflow.api.store import DatabaseUnavailable

    with pytest.raises(DatabaseUnavailable):
        MonitoringStore(None).health()


def test_empty_database_lists_and_counts():
    class Empty(FakeStore):
        def datasets(self, limit, offset):
            return [], 0

        def runs(self, limit, offset, dataset_id):
            return [], 0

    with TestClient(create_app(Empty())) as client:
        for path in ("/datasets", "/runs"):
            assert client.get(path).json() == {
                "data": [],
                "pagination": {"limit": 50, "offset": 0, "total": 0},
            }
        assert all(value == 0 for value in client.get("/overview").json()["data"].values())
