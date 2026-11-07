# Genuine demonstration evidence

Captured 2026-11-06 from GeoFlow 0.6.0 running locally against a dedicated native
PostgreSQL 17.11/PostGIS 3.6 review database. Only bundled synthetic inputs were used.
Run ID: a9d5da838497f5b087d6b7c1. Input 10, accepted 1, quarantined 9, issues 14,
issue-free fraction 0.1. Pipeline duration 0.422413 seconds; one execution only.

synthetic-run.json and quality.json were copied directly from the successful
pipeline output. issues.csv is the actual generated issue export. Source hashes
and application/package versions are in the report. Secrets are not included.
The database metrics capture a slightly earlier duration before transaction
publication; the pipeline report timer includes that publication and stops before
its final report write. Neither is browser render latency or a throughput benchmark.

Screenshots in ../screenshots were captured through the connected in-app browser
from the running Streamlit application backed by the API/database. Overview and
Data Quality were visually inspected. quality-rules.png is a second capture after
scrolling, not a fabricated or stitched visualization. No values were edited.
The first direct Chromium launch was blocked by sandbox permissions; the browser
connection succeeded. Temporary API/dashboard services were used on loopback
ports 18000/18501. Screenshots show one deliberately corrupted synthetic run.
