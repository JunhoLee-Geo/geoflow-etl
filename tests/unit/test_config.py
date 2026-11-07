"""Configuration contracts using local synthetic inputs only."""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from geoflow.config import Dataset, PipelineConfig, load_config
from geoflow.exceptions import ConfigError


def dataset_values(**overrides):
    return {
        "name": "synthetic",
        "path": "../data/points.csv",
        "format": "csv",
        "id_column": "id",
        "source_crs": "EPSG:4326",
        "source_description": "Synthetic fixture, not real observations",
        "license": "Synthetic test data",
        **overrides,
    }


@pytest.mark.parametrize("field", ["source_crs", "target_crs"])
def test_invalid_crs_is_rejected(field):
    with pytest.raises(ValidationError):
        Dataset(**dataset_values(**{field: "EPSG:999999999"}))


@pytest.mark.parametrize(
    "overrides",
    [
        {"unknown_setting": True},
        {"geometry_types": []},
        {"geometry_types": ["GeometryCollection"]},
        {"id_column": "__row_id"},
        {"required_columns": ["geometry"]},
        {"x_column": "same", "y_column": "same"},
        {"max_invalid_fraction": -0.01},
        {"max_invalid_fraction": 1.01},
        {"max_records": 0},
        {"table": "features; DROP SCHEMA geoflow"},
        {"format": "geojson", "source_crs": "EPSG:26917"},
    ],
)
def test_invalid_dataset_contract_is_rejected(overrides):
    with pytest.raises(ValidationError):
        Dataset(**dataset_values(**overrides))


def test_duplicate_dataset_names_and_unknown_root_keys_are_rejected():
    values = dataset_values()
    with pytest.raises(ValidationError):
        PipelineConfig(datasets=[values, values])
    with pytest.raises(ValidationError):
        PipelineConfig(datasets=[values], misspelled_output="ignored")


def test_paths_resolve_relative_to_yaml_not_working_directory(tmp_path, monkeypatch):
    config_dir = tmp_path / "project" / "configs"
    config_dir.mkdir(parents=True)
    config_file = config_dir / "sample.yml"
    config_file.write_text(yaml.safe_dump({"datasets": [dataset_values()]}), encoding="utf-8")
    unrelated = tmp_path / "elsewhere"
    unrelated.mkdir()
    monkeypatch.chdir(unrelated)
    config = load_config(config_file)
    assert config.datasets[0].path == (config_dir.parent / "data" / "points.csv").resolve()
    assert config.output_dir == (config_dir.parent / "reports").resolve()
    assert not config.datasets[0].path.exists()  # validate-config must not ingest the source.


@pytest.mark.parametrize("source", ["datasets: [", "datasets: []", "unknown: value", "null"])
def test_bad_yaml_or_schema_becomes_public_config_error(tmp_path, source):
    config_file = tmp_path / "bad.yml"
    config_file.write_text(source, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(config_file)


def test_missing_configuration_has_meaningful_exception(tmp_path):
    with pytest.raises(ConfigError):
        load_config(Path(tmp_path / "missing.yml"))
