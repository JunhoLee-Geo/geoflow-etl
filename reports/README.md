# Generated reports

Runs are written to `<dataset>/<content-addressed-run-id>/`. Generated reports are ignored by Git. The included sample runs are synthetic only.

- `source.csv` or `source.geojson`: byte-for-byte source snapshot.
- `accepted.geojson`: valid records in EPSG:4326, with 1-based `__row_id` for reconciliation. This is a diagnostic artifact until `report.json` says `succeeded`; failed quality gates can still produce it.
- `quarantine.jsonl`: every rejected parsed record, original values, 1-based source record ordinal, and all validation reasons.
- `report.json`: status, source/config/code identity, package versions, timestamps, duration, counts, rule counts, artifact checksums and database commit flag. Whole-document parsing failures retain the full source rather than claiming record counts.
- `.lock`: exists only while a process owns a run. After a crash, verify no owner is running before manually removing exactly that stale lock and retrying. GeoFlow never automatically steals locks.

Raw inputs and quarantine may contain sensitive data. Keep reports private and apply local filesystem permissions/retention appropriate to your data. Logs intentionally omit row values and connection URLs. Reports are not published to the portfolio automatically.
