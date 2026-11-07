from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from pyproj import CRS

from geoflow.exceptions import ConfigError, IngestionError
from geoflow.ingestion.downloads import validate_public_url
from geoflow.validation.models import ValidationRule


class Dataset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,47}$")
    path: Path | None = None
    url: str | None = None
    layer: str | None = None
    download_timeout_seconds: float = Field(default=20, gt=0, le=120)
    download_retries: int = Field(default=2, ge=0, le=5)
    expected_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    field_mapping: dict[str, str] = Field(default_factory=dict)
    trim_fields: list[str] = Field(default_factory=list)
    geometry_normalization: Literal["none", "promote_multi"] = "none"
    format: Literal["csv", "geojson", "gpkg"]
    id_column: str
    required_columns: list[str] = Field(default_factory=list)
    quality_rules: list[ValidationRule] = Field(default_factory=list)
    x_column: str = "longitude"
    y_column: str = "latitude"
    source_crs: str
    target_crs: str = "EPSG:26917"
    geometry_types: list[str] = Field(default_factory=lambda: ["Point"])
    max_invalid_fraction: float = Field(default=0, ge=0, le=1)
    max_source_bytes: int = Field(default=10_000_000, gt=0, le=100_000_000)
    max_records: int = Field(default=100_000, gt=0, le=1_000_000)
    source_description: str = Field(min_length=1)
    license: str = Field(min_length=1)
    sink: Literal["files", "postgis"] = "files"
    table: str = Field(default="features", pattern=r"^[a-z][a-z0-9_]{0,47}$")

    @field_validator("url")
    @classmethod
    def valid_url(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                validate_public_url(value)
            except IngestionError as exc:
                raise ValueError(str(exc)) from None
        return value

    @field_validator("source_crs", "target_crs")
    @classmethod
    def valid_crs(cls, value: str) -> str:
        try:
            crs = CRS.from_user_input(value)
            if crs.to_epsg() is None:
                raise ValueError("An EPSG CRS is required")
            return f"EPSG:{crs.to_epsg()}"
        except Exception as exc:
            raise ValueError("A supported EPSG CRS is required") from exc

    @field_validator("geometry_types")
    @classmethod
    def valid_types(cls, value: list[str]) -> list[str]:
        allowed = {
            "Point",
            "MultiPoint",
            "LineString",
            "MultiLineString",
            "Polygon",
            "MultiPolygon",
        }
        if not value or not set(value) <= allowed:
            raise ValueError("Unsupported geometry types")
        return value

    @model_validator(mode="after")
    def coherent(self) -> "Dataset":
        if (self.path is None) == (self.url is None):
            raise ValueError("Exactly one local path or HTTPS URL is required")
        if self.format == "gpkg" and not self.layer:
            raise ValueError("GeoPackage requires an explicit layer")
        codes = [rule.code for rule in self.quality_rules]
        if len(codes) != len(set(codes)):
            raise ValueError("Quality rule codes must be unique")
        if any(not code.startswith("custom_") for code in codes):
            raise ValueError(
                "Custom rule codes must start with custom_ to avoid built-in collisions"
            )
        targets = list(self.field_mapping.values())
        if len(targets) != len(set(targets)):
            raise ValueError("Field mapping destinations must be unique")
        if (
            self.id_column in self.field_mapping
            or self.id_column in self.field_mapping.values()
            or self.id_column in self.trim_fields
        ):
            raise ValueError("Original business identifiers cannot be renamed or trimmed")
        if any(
            not n or n.startswith("__") or n == "geometry"
            for n in [*self.field_mapping, *targets, *self.trim_fields]
        ):
            raise ValueError("Transformation fields cannot use reserved names")
        if self.format == "geojson" and self.source_crs != "EPSG:4326":
            raise ValueError("GeoJSON inputs must follow RFC7946 WGS84")
        names = [self.id_column, *self.required_columns, self.x_column, self.y_column]
        if any(not n or n.startswith("__") or n == "geometry" for n in names):
            raise ValueError("Column names cannot be empty or reserved")
        if self.format == "csv" and self.x_column == self.y_column:
            raise ValueError("Coordinate columns must differ")
        return self


class PipelineConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    output_dir: Path = Path("../reports")
    datasets: list[Dataset] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_names(self) -> "PipelineConfig":
        if len({d.name for d in self.datasets}) != len(self.datasets):
            raise ValueError("Dataset names must be unique")
        return self


def load_config(path: Path) -> PipelineConfig:
    """Paths are relative to the YAML file, independent of the working directory."""
    try:
        if path.stat().st_size > 1_000_000:
            raise ConfigError("Configuration exceeds 1 MB")
        config = PipelineConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        root = path.resolve().parent
        config.output_dir = (root / config.output_dir).resolve()
        for dataset in config.datasets:
            if dataset.path is not None:
                dataset.path = (root / dataset.path).resolve()
        return config
    except (OSError, ValueError, ValidationError, yaml.YAMLError) as exc:
        raise ConfigError(
            "Invalid configuration; check YAML, field names and CRS definitions"
        ) from exc
