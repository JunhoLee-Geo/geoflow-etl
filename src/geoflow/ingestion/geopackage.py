"""Read a bounded GeoPackage snapshot without GDAL silently skipping bad geometry.

Only ordinary feature tables with one integer primary key and an EPSG-backed CRS
are supported. The source bytes are copied into a private temporary directory,
opened read-only, and removed on every exit path. No extension is loaded.
"""

import base64
import math
import sqlite3
import struct
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from pyproj import CRS
from shapely import from_wkb
from shapely.errors import ShapelyError

from geoflow.config import Dataset
from geoflow.exceptions import IngestionError
from geoflow.ingestion.readers import Record


def _quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _json_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"encoding": "base64", "value": base64.b64encode(value).decode("ascii")}
    if isinstance(value, float) and not math.isfinite(value):
        return {"encoding": "nonfinite-float", "value": repr(value)}
    return value


def _geometry(blob: bytes, srs_id: int):
    if not isinstance(blob, bytes) or len(blob) < 8 or blob[:2] != b"GP" or blob[2] != 0:
        raise ValueError("Unsupported GeoPackage geometry header")
    flags = blob[3]
    envelope = (flags >> 1) & 7
    if flags & 0b11100000 or envelope > 4:
        raise ValueError("Extended/reserved geometry encoding is unsupported")
    endian = "<" if flags & 1 else ">"
    if struct.unpack(endian + "i", blob[4:8])[0] != srs_id:
        raise ValueError("Geometry CRS differs from layer metadata")
    offset = 8 + (0, 4, 6, 6, 8)[envelope] * 8
    if len(blob) <= offset:
        raise ValueError("Truncated geometry")
    geometry = from_wkb(blob[offset:])
    if bool(flags & 16) != geometry.is_empty:
        raise ValueError("Geometry empty flag disagrees with WKB")
    return geometry


def _read(connection: sqlite3.Connection, config: Dataset) -> list[Record]:
    layer = config.layer
    if not layer:
        raise IngestionError("GeoPackage requires an explicit layer")
    table = connection.execute(
        "SELECT type, sql FROM sqlite_schema WHERE name = ?", (layer,)
    ).fetchone()
    if not table or table[0] != "table" or "VIRTUAL TABLE" in (table[1] or "").upper():
        raise IngestionError("GeoPackage layer must be an ordinary feature table")
    metadata = connection.execute(
        "SELECT g.column_name, g.srs_id, s.organization, s.organization_coordsys_id, "
        "s.definition, c.data_type, c.srs_id FROM gpkg_geometry_columns g "
        "JOIN gpkg_spatial_ref_sys s ON s.srs_id = g.srs_id "
        "JOIN gpkg_contents c ON c.table_name = g.table_name WHERE g.table_name = ?",
        (layer,),
    ).fetchall()
    if len(metadata) != 1:
        raise IngestionError("GeoPackage layer has missing or ambiguous geometry/CRS metadata")
    geometry_column, srs_id, organization, epsg, definition, data_type, content_srs = metadata[0]
    if data_type != "features" or content_srs != srs_id or str(organization).upper() != "EPSG":
        raise IngestionError("GeoPackage requires consistent, known EPSG CRS metadata")
    try:
        declared = CRS.from_epsg(int(epsg))
        encoded = CRS.from_wkt(definition)
        configured = CRS.from_user_input(config.source_crs)
        if not declared.equals(encoded, ignore_axis_order=True) or not declared.equals(
            configured, ignore_axis_order=True
        ):
            raise ValueError("CRS mismatch")
    except Exception as exc:
        raise IngestionError("GeoPackage CRS is unknown or differs from source_crs") from exc
    columns = connection.execute(f"PRAGMA table_info({_quoted(layer)})").fetchall()
    primary = [column for column in columns if column[5]]
    if len(primary) != 1 or primary[0][2].upper() != "INTEGER":
        raise IngestionError("GeoPackage requires one INTEGER primary key to preserve feature IDs")
    names = [column[1] for column in columns]
    if geometry_column not in names or not {config.id_column, *config.required_columns} <= set(
        names
    ):
        raise IngestionError("GeoPackage layer is missing configured columns")
    primary_name = primary[0][1]
    rows = connection.execute(
        f"SELECT * FROM {_quoted(layer)} ORDER BY {_quoted(primary_name)} LIMIT ?",
        (config.max_records + 1,),
    ).fetchall()
    if not rows or len(rows) > config.max_records:
        raise IngestionError("GeoPackage layer is empty or exceeds the record limit")
    records = []
    for ordinal, row in enumerate(rows, 1):
        values = dict(zip(names, row, strict=True))
        raw = {name: _json_value(value) for name, value in values.items()}
        attributes = {name: value for name, value in raw.items() if name != geometry_column}
        failures = []
        if any(name.startswith("__") or name == "geometry" for name in attributes):
            failures.append("reserved_property")
        if any(isinstance(value, float) and not math.isfinite(value) for value in values.values()):
            failures.append("nonfinite_attribute")
        geometry = None
        try:
            geometry = _geometry(values[geometry_column], srs_id)
        except (ValueError, TypeError, ShapelyError, struct.error):
            failures.append("invalid_geometry_encoding")
        records.append(Record(ordinal, raw, attributes, geometry, failures, values[primary_name]))
    return records


def ingest_geopackage(content: bytes, config: Dataset) -> list[Record]:
    """Return every feature row, preserving FID and invalid binary geometry evidence."""
    if len(content) > config.max_source_bytes or not content.startswith(b"SQLite format 3\x00"):
        raise IngestionError("Expected a bounded GeoPackage SQLite file")
    try:
        with TemporaryDirectory(prefix="geoflow-gpkg-") as temporary:
            snapshot = Path(temporary) / "source.gpkg"
            snapshot.write_bytes(content)
            connection = sqlite3.connect(snapshot.as_uri() + "?mode=ro&immutable=1", uri=True)
            try:
                connection.execute("PRAGMA query_only = ON")
                connection.execute("PRAGMA trusted_schema = OFF")
                connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, config.max_source_bytes)
                return _read(connection, config)
            finally:
                connection.close()
    except IngestionError:
        raise
    except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
        raise IngestionError(
            "GeoPackage could not be read safely; source snapshot is retained"
        ) from exc
