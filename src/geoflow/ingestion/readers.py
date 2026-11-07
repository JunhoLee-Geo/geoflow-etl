"""Read each source record explicitly; malformed records remain traceable."""

import csv
import io
import json
from dataclasses import dataclass
from typing import Any

from shapely.errors import ShapelyError
from shapely.geometry import Point, shape
from shapely.geometry.base import BaseGeometry

from geoflow.config import Dataset
from geoflow.exceptions import IngestionError


@dataclass
class Record:
    row_id: int
    raw: dict[str, Any]
    attributes: dict[str, Any]
    geometry: BaseGeometry | None
    errors: list[str]
    source_feature_id: str | int | None = None


def ingest(content: bytes, config: Dataset) -> list[Record]:
    if config.format == "gpkg":
        from geoflow.ingestion.geopackage import ingest_geopackage

        return ingest_geopackage(content, config)
    records: list[Record] = []
    try:
        text = content.decode("utf-8-sig")
        if config.format == "csv":
            reader = csv.DictReader(io.StringIO(text), strict=True)
            fields = reader.fieldnames or []
            needed = {config.id_column, *config.required_columns, config.x_column, config.y_column}
            if len(fields) != len(set(fields)) or not needed <= set(fields):
                raise IngestionError("CSV headers are missing or duplicated")
            if any(f.startswith("__") or f == "geometry" for f in fields):
                raise IngestionError("CSV contains reserved columns")
            for number, row in enumerate(reader, 1):
                errors: list[str] = []
                geometry = None
                if None in row or any(v is None for v in row.values()):
                    errors.append("malformed_csv_row")
                try:
                    geometry = Point(float(row[config.x_column]), float(row[config.y_column]))
                except (ValueError, TypeError):
                    errors.append("invalid_coordinates")
                # JSON object keys must be strings; retain extra cells under a named key.
                raw = {str(k) if k is not None else "__extra_cells": v for k, v in row.items()}
                records.append(Record(number, raw, dict(raw), geometry, errors))
                if len(records) > config.max_records:
                    raise IngestionError("Source record limit exceeded")
        else:

            def reject_constant(value: str) -> None:
                raise ValueError("Non-finite JSON number")

            def unique_object(pairs: list) -> dict:
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError("Duplicate JSON keys")
                    result[key] = value
                return result

            doc = json.loads(text, parse_constant=reject_constant, object_pairs_hook=unique_object)
            if not isinstance(doc, dict) or doc.get("type") != "FeatureCollection" or "crs" in doc:
                raise IngestionError("Expected RFC7946 FeatureCollection without legacy CRS")
            features = doc.get("features")
            if not isinstance(features, list) or len(features) > config.max_records:
                raise IngestionError("Invalid feature collection or record limit exceeded")
            for number, feature in enumerate(features, 1):
                errors = []
                geometry = None
                raw = feature if isinstance(feature, dict) else {"__source_value": feature}
                attributes = raw.get("properties")
                if not isinstance(attributes, dict):
                    attributes = {}
                    errors.append("invalid_properties")
                if any(str(k).startswith("__") or k == "geometry" for k in attributes):
                    errors.append("reserved_property")
                try:
                    if raw.get("type") != "Feature" or "crs" in raw:
                        raise ValueError("Invalid feature")
                    encoded = raw.get("geometry")
                    if isinstance(encoded, dict):
                        if "crs" in encoded:
                            raise ValueError("Legacy CRS")
                        coords = encoded.get("coordinates", [])
                        polygons = (
                            [coords]
                            if encoded.get("type") == "Polygon"
                            else (coords if encoded.get("type") == "MultiPolygon" else [])
                        )
                        for polygon in polygons:
                            for ring in polygon:
                                if len(ring) < 4 or ring[0] != ring[-1]:
                                    raise ValueError("Polygon rings must already be closed")
                    geometry = shape(encoded)
                except (ValueError, TypeError, KeyError, AttributeError, ShapelyError, IndexError):
                    errors.append("invalid_geometry_encoding")
                feature_id = raw.get("id")
                if feature_id is not None and (
                    isinstance(feature_id, bool) or not isinstance(feature_id, (str, int))
                ):
                    errors.append("invalid_feature_id")
                    feature_id = None
                records.append(Record(number, raw, attributes, geometry, errors, feature_id))
    except IngestionError:
        raise
    except (UnicodeError, csv.Error, ValueError, TypeError) as exc:
        raise IngestionError(
            "Source cannot be parsed; the complete original snapshot is retained"
        ) from exc
    if not records:
        raise IngestionError("Empty source cannot replace a dataset")
    return records
