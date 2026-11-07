"""Explicit, lossless-to-source attribute and single-to-multi normalization."""

from dataclasses import replace
from typing import Any

from shapely.geometry import MultiLineString, MultiPoint, MultiPolygon

from geoflow.config import Dataset
from geoflow.exceptions import ConfigError
from geoflow.ingestion.readers import Record


def normalize_records(
    records: list[Record], config: Dataset
) -> tuple[list[Record], list[dict[str, Any]], list[str]]:
    # Copy fields rather than delete source attributes. Collisions are configuration errors.
    columns = {key for record in records for key in record.attributes}
    for source, target in config.field_mapping.items():
        if source != target and target in columns:
            raise ConfigError("Field mapping would overwrite an existing source attribute")
    result = []
    promoted = 0
    trimmed = 0
    constructors = {"Point": MultiPoint, "LineString": MultiLineString, "Polygon": MultiPolygon}
    for record in records:
        attributes = dict(record.attributes)
        errors = list(record.errors)
        for source, target in config.field_mapping.items():
            if source not in attributes:
                errors.append(f"mapping_source_missing:{source}")
            else:
                attributes[target] = attributes[source]
        for field in config.trim_fields:
            value = attributes.get(field)
            if not isinstance(value, str):
                errors.append(f"trim_requires_string:{field}")
            else:
                trimmed += value != value.strip()
                attributes[field] = value.strip()
        geometry = record.geometry
        if (
            config.geometry_normalization == "promote_multi"
            and geometry is not None
            and not geometry.is_empty
            and geometry.is_valid
            and geometry.geom_type in constructors
        ):
            geometry = constructors[geometry.geom_type]([geometry])
            promoted += 1
        result.append(replace(record, attributes=attributes, errors=errors, geometry=geometry))
    history: list[dict[str, Any]] = [
        {"operation": "copy_fields", "mapping": config.field_mapping},
        {"operation": "trim_strings", "fields": config.trim_fields, "changed_values": trimmed},
        {
            "operation": "geometry_normalization",
            "mode": config.geometry_normalization,
            "changed_features": promoted,
        },
    ]
    warnings = (
        ["Source attributes are retained alongside mapped fields"] if config.field_mapping else []
    )
    return result, history, warnings
