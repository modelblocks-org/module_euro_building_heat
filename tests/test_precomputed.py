"""Check scientific cache selection and lossless windowing of published grids."""

import copy
import sys
from pathlib import Path

import geopandas as gpd
import jsonschema
import numpy as np
import pandas as pd
import pytest
import rasterio
import yaml
from shapely.geometry import box

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "workflow/scripts"))
from _floor_area import residential_floor_area  # noqa: E402
from _precomputed import matches_precomputed  # noqa: E402
from prepare_precomputed_rasters import crop_grid  # noqa: E402


@pytest.fixture
def settings():
    """Load editable defaults and the independent publication snapshot."""
    return (
        yaml.safe_load((ROOT / "config/config.yaml").read_text()),
        yaml.safe_load(
            (ROOT / "workflow/internal/precomputed_defaults.yaml").read_text()
        ),
    )


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("spatial_weights", "population", "share"), 0.6),
        (("spatial_weights", "age", "multipliers", "before_1991"), 1.5),
        (("buildings_eubucco", "source"), "lightweight"),
        (("buildings_microsoft", "minimum_building_count"), 3000),
        (("population", "epoch"), 2020),
        (("raster", "dtype"), "float64"),
        (("eurostat", "useful_to_gross_ratio"), 1.3),
        (("data_proxies", "floor_area", "countries", "NEW"), ["FRA"]),
    ],
)
def test_changed_assumptions_rebuild(settings, path, value):
    """Changes and added proxy countries must invalidate the publication."""
    config, baseline = settings
    assert matches_precomputed(config, baseline)
    parent = config
    for key in path[:-1]:
        parent = parent[key]
    parent[path[-1]] = value
    assert not matches_precomputed(config, baseline)


def test_downstream_and_execution_settings_reuse(settings):
    """Execution tuning and downstream heat assumptions keep published grids."""
    config, baseline = settings
    original = copy.deepcopy(baseline)
    config["population"]["chunk_size"] = 256
    config["processing"]["nuts3_batches"] = 1
    config["spatial_weights"]["hdd"]["elasticity"] = 0
    config["heat_pump"]["correction_factor"] = 0.9
    config["weather_years"]["start"] = 2020
    config["plotting"]["max_size"] = 100
    assert matches_precomputed(config, baseline)
    assert baseline == original


def test_crop_values_and_coverage(tmp_path, settings):
    """Cropping keeps per-cell values and metadata and rejects missing coverage."""
    config, _ = settings
    source, target = tmp_path / "source.tif", tmp_path / "target.tif"
    profile = dict(
        driver="GTiff",
        width=4,
        height=4,
        count=1,
        dtype="float32",
        crs="EPSG:3035",
        transform=rasterio.Affine(100, 0, 4000000, 0, -100, 3000400),
        nodata=0,
    )
    values = np.arange(1, 17, dtype="float32").reshape(4, 4)
    with rasterio.open(source, "w", **profile) as dst:
        dst.write(values, 1)
        dst.set_band_description(1, "building_count")
        dst.set_band_unit(1, "buildings/ha")
    shapes = gpd.GeoDataFrame(
        geometry=[box(4000100, 3000100, 4000300, 3000300)], crs="EPSG:3035"
    )
    crop_grid(source, target, shapes, config["raster"])
    with rasterio.open(target) as dst:
        np.testing.assert_array_equal(dst.read(1), values[1:3, 1:3])
        assert dst.res == (100, 100)
        assert dst.nodata == 0
        assert dst.descriptions == ("building_count",)
        assert dst.units == ("buildings/ha",)
    shapes.geometry = [box(3999900, 3000100, 4000300, 3000300)]
    with pytest.raises(ValueError, match="coverage"):
        crop_grid(source, target, shapes, config["raster"])


def test_surface_volume_source_compatibility(settings):
    """Keep both compactness methods, but reject measured perimeters from points."""
    config, _ = settings
    schema = yaml.safe_load((ROOT / "workflow/internal/config.schema.yaml").read_text())
    jsonschema.validate(config, schema)
    config["buildings_eubucco"]["source"] = "lightweight"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(config, schema)
    config["spatial_weights"]["surface_volume"]["method"] = "equivalent_square"
    jsonschema.validate(config, schema)


@pytest.mark.parametrize("rooms_ge9", [10, 12])
def test_census_room_fallback(settings, rooms_ge9):
    """Exact room counts survive config cleanup; the open-ended estimate still varies."""
    config, _ = settings
    config["eurostat"]["rooms"]["GE9"] = rooms_ge9
    census = pd.DataFrame(
        [
            ("A", "SQM_LT30", "TOTAL", 2),
            ("A", "TOTAL", "1", 100),
            ("B", "SQM_LT30", "TOTAL", 0),
            ("B", "TOTAL", "1", 2),
            ("B", "TOTAL", "GE9", 3),
        ],
        columns=["geo", "area", "n_room", "value"],
    ).assign(freq="A", building="TOTAL", unit="NR")
    result = residential_floor_area(census, config["eurostat"])
    assert result["A"] == pytest.approx(2 * 25 * 1.2)
    assert result["B"] == pytest.approx((2 + 3 * rooms_ge9) * 20 * 1.2)
