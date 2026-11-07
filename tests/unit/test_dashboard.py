import json

import httpx
import pytest

from geoflow.dashboard.adapter import MonitorClient, MonitoringError, issue_csv, json_report


def client(response):
    return MonitorClient(transport=httpx.MockTransport(lambda request: response))


def test_empty_page_is_not_synthetic_data():
    expected = {"data": [], "pagination": {"limit": 50, "offset": 0, "total": 0}}
    assert client(httpx.Response(200, json=expected)).get("/runs", limit=50, offset=0) == expected


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://user:secret@localhost/db",
        "http://user:secret@localhost",
        "http://localhost/?token=secret",
        "http://localhost/path",
        "file:///tmp",
    ],
)
def test_configuration_rejects_credentials_and_non_api_origins(url):
    with pytest.raises(MonitoringError):
        MonitorClient(url)


@pytest.mark.parametrize("status", [301, 403, 404, 422, 500, 503])
def test_http_errors_do_not_leak_backend_details(status):
    with pytest.raises(MonitoringError) as error:
        client(httpx.Response(status, text="password=secret")).get("/overview")
    assert "secret" not in str(error.value)


def test_oversized_body_rejected():
    with pytest.raises(MonitoringError, match="size limit"):
        client(httpx.Response(200, content=b"x" * 1_000_001)).get("/overview")


@pytest.mark.parametrize("response", [[], {"wrong": []}, "not json"])
def test_malformed_responses_rejected(response):
    with pytest.raises(MonitoringError):
        client(httpx.Response(200, json=response)).get("/runs")


@pytest.mark.parametrize(
    "params", [{"limit": 101}, {"limit": 0}, {"offset": -1}, {"offset": 10001}]
)
def test_pagination_bounds(params):
    with pytest.raises(MonitoringError):
        client(httpx.Response(200, json={"data": []})).get("/runs", **params)


def test_only_monitoring_paths_are_allowed():
    with pytest.raises(MonitoringError):
        client(httpx.Response(200, json={"data": []})).get("/run-pipeline")


def test_transport_errors_are_sanitized():
    def fail(request):
        raise httpx.ConnectError("secret database credentials")

    with pytest.raises(MonitoringError) as error:
        MonitorClient(transport=httpx.MockTransport(fail)).get("/health")
    assert "secret" not in str(error.value)


def test_export_contains_page_only_and_no_private_details():
    csv = issue_csv(
        [
            {
                "issue_number": 1,
                "code": "=danger",
                "severity": "ERROR",
                "details": {"password": "secret"},
                "raw_record": "private",
            }
        ]
    )
    assert "'=danger" in csv
    assert "secret" not in csv and "private" not in csv
    assert len(csv.splitlines()) == 2
    report = json.loads(json_report({"run_id": "test"}, {"pass_rate": None}))
    assert report == {"run": {"run_id": "test"}, "quality": {"pass_rate": None}}


def test_streamlit_empty_overview_renders(monkeypatch):
    from pathlib import Path

    from streamlit.testing.v1 import AppTest

    def fake_get(self, path, **params):
        if path == "/overview":
            return {
                "data": dict.fromkeys(
                    [
                        "total_runs",
                        "successful_runs",
                        "failed_runs",
                        "input_records",
                        "accepted_records",
                        "quarantined_records",
                        "published_records",
                        "unmeasured_runs",
                    ],
                    0,
                )
            }
        return {"data": [], "pagination": {"limit": 10, "offset": 0, "total": 0}}

    monkeypatch.setattr(MonitorClient, "get", fake_get)
    app = AppTest.from_file(str(Path(__file__).parents[2] / "src/geoflow/dashboard/app.py"))
    app.run(timeout=15)
    assert not app.exception
    assert len(app.metric) == 6
    assert app.info[0].value == "No pipeline executions have been recorded."
    for page in ["Run History", "Data Quality", "Dataset Explorer", "Reports"]:
        app.sidebar.radio[0].set_value(page).run()
        assert not app.exception
        assert app.info


def test_streamlit_service_error_is_visible_without_traceback(monkeypatch):
    from pathlib import Path

    from streamlit.testing.v1 import AppTest

    def fail(self, path, **params):
        raise MonitoringError("Unable to read monitoring data.")

    monkeypatch.setattr(MonitorClient, "get", fail)
    app = AppTest.from_file(str(Path(__file__).parents[2] / "src/geoflow/dashboard/app.py"))
    app.run(timeout=15)
    assert not app.exception
    assert app.error[0].value == "Unable to read monitoring data."
