"""Start with: streamlit run src/geoflow/dashboard/app.py --server.address 127.0.0.1."""

import math
import os
from datetime import UTC, datetime

import pandas as pd
import pydeck as pdk
import streamlit as st
from shapely.geometry import shape

from geoflow.dashboard.adapter import MonitorClient, MonitoringError, issue_csv, json_report


def page_data(client, path, key, **params):
    offset = st.number_input(
        "Record offset",
        min_value=0,
        max_value=10_000,
        step=50,
        value=0,
        key=key,
        help="Pages contain at most 50 records.",
    )
    response = client.get(path, limit=50, offset=int(offset), **params)
    pagination = response["pagination"]
    st.caption(
        f"{len(response['data'])} displayed · {pagination['total']:,} total · offset {offset:,}"
    )
    return response


def choose_run(client, key):
    response = page_data(client, "/runs", key)
    rows = response["data"]
    if not rows:
        st.info("No runs on this page. Execute ETL separately or choose an earlier offset.")
        return None
    choices = {r["run_id"]: r for r in rows}
    return st.selectbox(
        "Run",
        list(choices),
        key=key + "_run",
        format_func=lambda value: (
            f"{choices[value]['dataset_id']} · {choices[value]['status']} · {value}"
        ),
    )


def overview(client):
    values = client.get("/overview")["data"]
    columns = st.columns(3)
    for column, key, label in zip(
        columns,
        ["total_runs", "successful_runs", "failed_runs"],
        ["Pipeline runs", "Successful", "Failed"],
        strict=True,
    ):
        column.metric(label, f"{values[key]:,}")
    columns = st.columns(3)
    for column, key, label in zip(
        columns,
        ["input_records", "accepted_records", "quarantined_records"],
        ["Input records", "Accepted candidates", "Quarantined"],
        strict=True,
    ):
        column.metric(label, f"{values[key]:,}")
    st.caption(
        f"Counts sum recorded runs, not distinct features across snapshots. "
        f"{values['unmeasured_runs']:,} runs have no measured record counts. "
        f"{values['published_records']:,} records were published across runs."
    )
    st.subheader("Recent executions")
    rows = client.get("/runs", limit=10, offset=0)["data"]
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    else:
        st.info("No pipeline executions have been recorded.")


def history(client):
    response = page_data(client, "/runs", "history_offset")
    if response["data"]:
        st.dataframe(pd.DataFrame(response["data"]), hide_index=True, use_container_width=True)
    else:
        st.info("No executions on this page.")


def quality(client):
    run_id = choose_run(client, "quality_offset")
    if not run_id:
        return
    data = client.get(f"/runs/{run_id}/quality")["data"]
    left, right = st.columns(2)
    rate = data["pass_rate"]
    left.metric("Issue-free pass rate", "Not measured" if rate is None else f"{rate:.1%}")
    right.metric(
        "Quarantined records",
        data["quarantined_records"] if data["quarantined_records"] is not None else "Not measured",
    )
    st.caption(
        "Pass rate counts records with no issues. Report-only warnings can still be accepted."
    )
    left, right = st.columns(2)
    with left:
        st.subheader("Failures by rule")
        if data["rule_counts"]:
            st.bar_chart(
                pd.DataFrame.from_dict(data["rule_counts"], orient="index", columns=["Records"])
            )
        else:
            st.info("No rule failures recorded.")
    with right:
        st.subheader("Issue severity")
        if data["severity_counts"]:
            st.bar_chart(
                pd.DataFrame.from_dict(data["severity_counts"], orient="index", columns=["Issues"])
            )
        else:
            st.info("No validation issues recorded.")


def explorer(client):
    datasets = page_data(client, "/datasets", "dataset_offset")["data"]
    if not datasets:
        st.info("No datasets on this page.")
        return
    dataset_id = st.selectbox("Dataset", [row["dataset_id"] for row in datasets])
    summary = client.get(f"/datasets/{dataset_id}/summary")["data"]
    st.dataframe(pd.DataFrame([summary]), hide_index=True, use_container_width=True)
    st.caption(
        "Only safe format/CRS metadata is exposed. Source URLs and raw attributes are withheld."
    )
    if not st.checkbox("Load bounded spatial preview"):
        return
    preview = client.get(f"/datasets/{dataset_id}/preview", limit=100)["data"]
    st.caption(
        f"{preview['returned_features']} shown / {preview['sampled_features']} sampled; "
        f"{preview['omitted_features']} omitted. "
        f"Simplified at {preview['simplification_tolerance_m']} m. "
        "Sample preview only, not a complete export or a new analytical result."
    )
    geojson = preview["geojson"]
    if not geojson["features"]:
        st.info("No bounded features are available to preview.")
        return
    bounds = [shape(feature["geometry"]).bounds for feature in geojson["features"]]
    west, south = min(b[0] for b in bounds), min(b[1] for b in bounds)
    east, north = max(b[2] for b in bounds), max(b[3] for b in bounds)
    zoom = max(1, min(16, math.log2(360 / max(east - west, north - south, 0.001)) - 1))
    deck = pdk.Deck(
        map_style=None,
        map_provider=None,
        initial_view_state=pdk.ViewState(
            longitude=(west + east) / 2, latitude=(south + north) / 2, zoom=zoom
        ),
        layers=[
            pdk.Layer(
                "GeoJsonLayer",
                data=geojson,
                filled=True,
                stroked=True,
                get_fill_color=[34, 211, 238, 120],
                get_line_color=[56, 189, 248],
                get_point_radius=25,
                point_radius_min_pixels=4,
                line_width_min_pixels=2,
            )
        ],
    )
    st.pydeck_chart(deck, use_container_width=True)
    st.caption(
        "Geometry-only preview, EPSG:4326. No external tiles. Bounds derive from returned data."
    )


def reports(client):
    run_id = choose_run(client, "reports_offset")
    if not run_id:
        return
    run = client.get(f"/runs/{run_id}")["data"]
    result = client.get(f"/runs/{run_id}/quality")["data"]
    st.subheader("Execution summary")
    st.dataframe(pd.DataFrame([run]), hide_index=True, use_container_width=True)
    st.download_button(
        "Download quality summary JSON",
        json_report(run, result),
        file_name=f"{run_id}_quality.json",
        mime="application/json",
    )
    page = page_data(client, f"/runs/{run_id}/issues", "report_issues_offset")
    st.caption(
        "CSV contains only this issue page. JSON contains aggregate metrics, not raw records."
    )
    st.dataframe(pd.DataFrame(page["data"]), hide_index=True, use_container_width=True)
    st.download_button(
        "Download this issue page CSV",
        issue_csv(page["data"]),
        file_name=f"{run_id}_issues_offset_{page['pagination']['offset']}.csv",
        mime="text/csv",
    )


def main():
    st.set_page_config(page_title="GeoFlow · Monitoring", page_icon="◈", layout="wide")
    st.sidebar.title("GeoFlow")
    st.sidebar.caption("Geospatial ETL monitoring")
    page = st.sidebar.radio(
        "Navigation", ["Overview", "Run History", "Data Quality", "Dataset Explorer", "Reports"]
    )
    st.sidebar.button("Refresh stored data")
    st.sidebar.caption(
        "Read-only. Updates are fetched on interaction or refresh; this is not a live stream."
    )
    st.title(page)
    st.caption(
        "Inspect recorded pipeline executions, validation outcomes and approved spatial previews."
    )
    try:
        client = MonitorClient(os.getenv("GEOFLOW_API_URL", "http://127.0.0.1:8000"))
        with st.spinner("Reading stored monitoring data…"):
            {
                "Overview": overview,
                "Run History": history,
                "Data Quality": quality,
                "Dataset Explorer": explorer,
                "Reports": reports,
            }[page](client)
        st.caption(f"Fetched at {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    except MonitoringError as exc:
        st.error(str(exc))
        st.info(
            "Start the local API and verify its read-only database configuration, then refresh."
        )
    except (KeyError, TypeError, ValueError):
        st.error("Monitoring data did not match the expected format. Check service versions.")


if __name__ == "__main__":
    main()
