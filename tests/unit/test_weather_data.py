"""Focused tests for Earth Data Hub weather processing."""

import sys
from datetime import datetime
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rioxarray  # noqa: F401
import xarray as xr
from shapely.geometry import box

SCRIPTS = Path(__file__).parents[2] / "workflow" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import download_era5_data as download  # noqa: E402
import population_per_gridbox as population  # noqa: E402
import process_gridded_weather_data as weather  # noqa: E402


def _edh_dataset() -> xr.Dataset:
    time = pd.to_datetime(
        ["2017-12-24 23:00", "2017-12-25 00:00", "2019-01-05 23:00", "2019-01-06 00:00"]
    )
    latitude = [51.0, 50.75, 50.5]
    longitude = [0.0, 0.25, 359.75]
    shape = (len(time), len(latitude), len(longitude))
    return xr.Dataset(
        {
            "t2m": (("valid_time", "latitude", "longitude"), np.full(shape, 274.15)),
            "u10": (("valid_time", "latitude", "longitude"), np.full(shape, 3.0)),
            "v10": (("valid_time", "latitude", "longitude"), np.full(shape, 4.0)),
            "stl1": (("valid_time", "latitude", "longitude"), np.full(shape, 278.15)),
            "unused": (("valid_time", "latitude", "longitude"), np.zeros(shape)),
        },
        coords={"valid_time": time, "latitude": latitude, "longitude": longitude},
    )


def test_select_and_convert_edh_weather():
    """EDH coordinates and variables retain the workflow's output contract."""
    selected = download.select_edh_weather(
        _edh_dataset(),
        bounds=(-0.25, 50.5, 0.25, 51.0),
        start=datetime(2017, 12, 25),
        end=datetime(2019, 1, 5, 23),
    )

    assert list(selected.data_vars) == download.EDH_VARIABLES
    np.testing.assert_array_equal(selected.lon, [-0.25, 0.0, 0.25])
    np.testing.assert_array_equal(selected.lat, [50.5, 50.75, 51.0])
    np.testing.assert_array_equal(
        selected.time,
        np.array(["2017-12-25", "2019-01-05T23:00"], dtype="datetime64[h]"),
    )

    outputs = weather.convert_era5_to_module_datasets(selected)
    assert set(outputs) == {"grid", "temperature", "wind10m", "tsoil5"}
    assert outputs["temperature"].temperature.dims == ("site", "time")
    np.testing.assert_allclose(outputs["temperature"].temperature, 1.0)
    np.testing.assert_allclose(outputs["wind10m"].wind10m, 5.0)
    np.testing.assert_allclose(outputs["tsoil5"].tsoil5, 5.0)
    assert outputs["grid"].sizes == {"site": 9}


def test_weather_bounds_use_native_grid_buffer():
    """Download bounds align to and buffer the native 0.25-degree grid."""
    shapes = gpd.GeoDataFrame(geometry=[box(0.1, 50.1, 0.4, 50.4)], crs="EPSG:4326")
    assert download.weather_bounds(shapes) == (-0.25, 49.75, 0.75, 50.75)


def test_download_writes_one_reusable_file(monkeypatch, tmp_path):
    """The download stage materialises the complete EDH subset once."""
    shapes = gpd.GeoDataFrame(geometry=[box(-0.1, 50.6, 0.1, 50.9)], crs="EPSG:4326")
    open_dataset = xr.open_dataset
    monkeypatch.setattr(download.gpd, "read_parquet", lambda _: shapes)
    monkeypatch.setattr(
        download.xr, "open_dataset", lambda *args, **kwargs: _edh_dataset()
    )
    output = tmp_path / "heat.nc"

    download.download_era5_data("shapes.parquet", output, [2018])

    with open_dataset(output) as dataset:
        assert list(dataset.data_vars) == download.EDH_VARIABLES
        assert dataset.attrs["dataset"] == "era5-edh"
        assert dataset.attrs["grid_degrees"] == 0.25


def test_process_records_edh_metadata(monkeypatch):
    """All module outputs identify EDH and its native grid."""
    captured = {}
    selected = download.select_edh_weather(
        _edh_dataset(),
        bounds=(-0.25, 50.5, 0.25, 51.0),
        start=datetime(2017, 12, 25),
        end=datetime(2019, 1, 5, 23),
    )
    monkeypatch.setattr(weather.xr, "open_dataset", lambda *args, **kwargs: selected)
    monkeypatch.setattr(
        weather,
        "write_module_weather_outputs",
        lambda outputs, _: captured.update(outputs),
    )

    weather.process_gridded_weather_data("era5.nc", {}, [2018])

    for dataset in captured.values():
        assert dataset.attrs["dataset"] == "era5-edh"
        assert dataset.attrs["date_from"] == "2017-12-25"
        assert dataset.attrs["date_to"] == "2019-01-05"
        assert dataset.attrs["grid_degrees"] == 0.25


def test_population_overlay_uses_raster_crs(monkeypatch, tmp_path):
    """Weather cells and shapes are overlaid in the population raster CRS."""
    coordinate_ds = xr.Dataset(
        {"lat": ("site", [50.0]), "lon": ("site", [5.0])},
        coords={"site": [0]},
        attrs={"grid_degrees": 0.25},
    )
    locations = gpd.GeoDataFrame(
        {"shape_id": ["NLD"]}, geometry=[box(4.95, 49.95, 5.05, 50.05)], crs="EPSG:4326"
    )
    raster = xr.DataArray([[1.0]], dims=("y", "x")).rio.write_crs("ESRI:54009")
    target_crs = raster.rio.crs

    monkeypatch.setattr(
        population.xr, "open_dataset", lambda *args, **kwargs: coordinate_ds
    )
    monkeypatch.setattr(
        population.gpd, "read_parquet", lambda *args, **kwargs: locations
    )
    monkeypatch.setattr(
        population.rioxarray, "open_rasterio", lambda *args, **kwargs: raster
    )
    original_overlay = population.gpd.overlay

    def checked_overlay(gridbox, projected_locations):
        assert gridbox.crs == target_crs
        assert projected_locations.crs == target_crs
        return original_overlay(gridbox, projected_locations)

    def aggregate(_raster, polygons):
        assert polygons.crs == target_crs
        return polygons.assign(population=100.0)

    monkeypatch.setattr(population.gpd, "overlay", checked_overlay)
    monkeypatch.setattr(population, "_aggregate_population_to_polygons", aggregate)
    population.population_on_weather_grid(
        "population.tif",
        "locations.parquet",
        "grid.nc",
        "lat",
        "lon",
        tmp_path / "population.nc",
    )

    assert (tmp_path / "population.nc").exists()
