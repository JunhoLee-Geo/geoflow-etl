# Public source verification

Checked 2026-11-05. **Live integration is pending.** No invented API endpoint,
unverified source CRS, or real public dataset is included in the example runs.

The candidate is Grand River Transit stops, a bounded point dataset suitable for
the Waterloo demonstration. Primary publisher pages:

- [GRT open data](https://www.grt.ca/about-grt/open-data/) links its stop dataset
  and identifies GTFS as the schedule format.
- [Official GRT stops catalogue](https://rowopendata-rmw.opendata.arcgis.com/datasets/grt-stops-2/explore)
  is the linked catalogue. Its JavaScript metadata was not available to the
  research client, so no download/service endpoint has been inferred.
- [Region open data and licence](https://www.regionofwaterloo.ca/government-and-council/transparency-and-accountability/open-data/)
  contains the regional reuse licence. The page has inconsistent version labels
  (v2.0 heading and v1.0 text); confirm the applicable dataset licence before use.

| Required evidence | Status |
| --- | --- |
| Publisher and catalogue URL | Verified from GRT publisher page |
| Dataset-specific licence/version | Pending clarification |
| Schema, stable source ID, coordinate fields | Pending actual export/metadata |
| Export CRS | Pending; general regional UTM guidance is not dataset evidence |
| Update frequency | Pending; general “as required” policy is not a stop-specific cadence |
| Download method and final URL | Pending; no endpoint configured |

To integrate: inspect the catalogue metadata and a small official export; record
the verified schema, EPSG CRS, licence, update statement and final download URL
here. Save a closed export outside Git under `data/raw/`, then configure `path`,
`format`, explicit `source_crs`, `id_column`, CSV x/y fields or GeoPackage `layer`.
After verification, replace `path` with the publisher's final public HTTPS `url`.
Set a byte/record limit and optionally pin `expected_sha256` for reproducibility.
Do not commit downloaded raw datasets or credentials. The local-file adapter is
fully usable while publisher metadata/download access remains unresolved.

Bundled CSV, GeoJSON and GeoPackage fixtures are synthetic and have an explicitly
known EPSG:4326 CRS. Their coordinates and counts are test inputs only.
