"""Synthetic conservation checks without downloads or API credentials.

Run with the module environment: python -m unittest discover -s tests
-p test_heat_sink_outputs.py.
"""

import sys
import tempfile
import unittest
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import xarray as xr
from rasterio.transform import from_origin
from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "workflow/scripts"))

from _timeseries import write_hourly_parquet  # noqa: E402
from gridded_heat_demand import write_heat_demand_rasters  # noqa: E402
from heat_demand_final_timeseries import (  # noqa: E402
    heat_sink_profile,
    prepare_annual_demand,
    scale_heat_demand_profiles,
)


class HeatSinkOutputsTest(unittest.TestCase):
    """Verify spatial and temporal exports against known annual demand."""

    def test_rasters_preserve_sink_shape_and_year_totals(self):
        """Use sector proxies for hot water, independently of space heat/HDD."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            transform = from_origin(0, 200, 100, 100)
            household = np.array([[10, 0, 5, 20, 5, 5, 99]] * 2, dtype=float)
            commercial = np.array([[0, 10, 0, 0, 0, 14, 99]] * 2, dtype=float)
            residential_support = np.array([[0, 1, 3, 0, 5, 0, 99]] * 2, dtype=float)
            # A shifted commercial grid exercises resampling onto the output grid.
            commercial_support = np.array([[0, 2, 0, 0, 1, 0, 0, 99]] * 2, dtype=float)
            inputs = {}
            for name, data, count, source_transform in [
                ("household", household, 2, transform),
                ("commercial", commercial, 2, transform),
                ("residential_support", residential_support, 1, transform),
                (
                    "commercial_support",
                    commercial_support,
                    1,
                    from_origin(-100, 200, 100, 100),
                ),
            ]:
                inputs[name] = root / f"{name}.tif"
                with rasterio.open(
                    inputs[name],
                    "w",
                    driver="GTiff",
                    width=data.shape[1],
                    height=2,
                    count=count,
                    dtype="float64",
                    crs="EPSG:3035",
                    transform=source_transform,
                    nodata=0,
                ) as output:
                    for band in range(1, count + 1):
                        output.write(data * band, band)
                        if count == 2:
                            output.update_tags(
                                band, demand_year=2019 + band, weather_year=2022 + band
                            )
            shapes = gpd.GeoDataFrame(
                {"shape_id": ["a", "b"]},
                geometry=[box(0, 0, 300, 200), box(300, 0, 600, 200)],
                crs="EPSG:3035",
            )
            records = []
            for factor, year in enumerate([2020, 2021], start=1):
                for shape_id, values in {
                    "a": [30, 20, 40, 10],
                    "b": [60, 28, 50, 20],
                }.items():
                    for (sink, category), energy in zip(
                        [
                            ("space_heat", "household"),
                            ("space_heat", "commercial"),
                            ("hot_water", "household"),
                            ("hot_water", "commercial"),
                        ],
                        values,
                        strict=True,
                    ):
                        records.append(
                            [sink, category, year, shape_id, energy * factor / 1e6]
                        )
            annual = pd.DataFrame(
                records,
                columns=["end_use", "category", "year", "shape_id", "heat_demand_twh"],
            )
            paths = {sink: root / f"{sink}.tif" for sink in ["space_heat", "hot_water"]}

            def write():
                write_heat_demand_rasters(
                    annual,
                    shapes,
                    inputs["household"],
                    inputs["residential_support"],
                    paths,
                    inputs["commercial"],
                    inputs["commercial_support"],
                )

            write()
            expected = {
                "space_heat": np.array([[10, 10, 5, 20, 5, 19, 0]] * 2),
                "hot_water": np.array([[5, 5, 15, 10, 25, 0, 0]] * 2),
            }
            for sink, path in paths.items():
                with rasterio.open(path) as output:
                    assert output.transform == transform
                    assert output.crs == rasterio.crs.CRS.from_epsg(3035)
                    assert output.nodata == 0
                    assert output.count == 2
                    assert output.tags()["end_use"] == sink
                    for band in output.indexes:
                        np.testing.assert_allclose(
                            output.read(band), expected[sink] * band
                        )
                        assert output.tags(band)["demand_year"] == str(2019 + band)
                        assert output.tags(band)["weather_year"] == str(2022 + band)
                        assert output.units[band - 1] == "MWh/cell"
            # Change the spatial HDD pattern as well as the shape totals in the
            # intermediate space-heat raster: hot water must be unaffected.
            with rasterio.open(inputs["household"], "r+") as output:
                for band in output.indexes:
                    output.write(np.ones_like(household) * band * 100, band)
            write()
            with rasterio.open(paths["hot_water"]) as output:
                for band in output.indexes:
                    np.testing.assert_allclose(
                        output.read(band), expected["hot_water"] * band
                    )
            with rasterio.open(paths["space_heat"]) as output:
                for band in output.indexes:
                    values = output.read(band)
                    np.testing.assert_allclose(values[:, :3].sum(), 50 * band)
                    np.testing.assert_allclose(values[:, 3:6].sum(), 88 * band)
                    assert values[:, 6].sum() == 0
            for category, name in [
                ("household", "residential_support"),
                ("commercial", "commercial_support"),
            ]:
                with rasterio.open(inputs[name], "r+") as output:
                    original = output.read(1)
                    output.write(np.zeros_like(original), 1)
                with self.assertRaisesRegex(  # noqa: PT027
                    ValueError, f"No {category} hot_water support"
                ):
                    write()
                # Zero demand permits zero support.
                mask = (annual.end_use == "hot_water") & (annual.category == category)
                original_demand = annual.loc[mask, "heat_demand_twh"].copy()
                annual.loc[mask, "heat_demand_twh"] = 0
                write()
                with rasterio.open(inputs[name], "r+") as output:
                    output.write(original, 1)
                annual.loc[mask, "heat_demand_twh"] = original_demand

    def test_profiles_reconstruct_hourly_demand_including_leap_year(self):
        """Normalise each sink after combining building categories by demand."""
        times = pd.date_range("2023-01-01", "2025-01-01", freq="h", inclusive="left")
        coordinates = {
            "time": times,
            "building": ["COM", "SFH", "MFH"],
            "id": ["a", "b"],
        }
        pattern = np.arange(len(times))[:, None, None] % 24 + 1
        values = pattern * np.array([1, 2, 3])[None, :, None] * np.ones((1, 1, 2))
        unscaled = xr.Dataset(
            {
                "space_heat": (("time", "building", "id"), values),
                "hot_water": (("time", "building", "id"), values[::-1]),
            },
            coords=coordinates,
        )
        shares = xr.DataArray(
            [[1, 1], [0.25, 0.75], [0.75, 0.25]],
            dims=["building", "id"],
            coords={key: coordinates[key] for key in ["building", "id"]},
        )
        annual = pd.DataFrame(
            [
                [sink, category, year, shape_id, energy if shape_id == "a" else 0]
                for sink, energy in [("space_heat", 0.003), ("hot_water", 0.001)]
                for category in ["household", "commercial"]
                for year in [2020, 2021]
                for shape_id in ["a", "b"]
            ],
            columns=["end_use", "category", "year", "shape_id", "heat_demand_twh"],
        )
        mapping = {2023: 2020, 2024: 2021}
        scaled = scale_heat_demand_profiles(
            prepare_annual_demand(annual), unscaled, shares, mapping
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            timezones_path = root / "timezones.parquet"
            pd.DataFrame(
                {"shape_id": ["a", "b"], "timezone": ["Europe/Amsterdam", "UTC"]}
            ).to_parquet(timezones_path)
            reconstructed = []
            for sink, energy in [("space_heat", 6000), ("hot_water", 2000)]:
                profile = heat_sink_profile(scaled, sink)
                np.testing.assert_allclose(
                    profile.groupby(profile.index.year).sum(), [[1, 0], [1, 0]]
                )
                assert len(profile.loc["2024"]) == 8784
                reconstructed.append(profile * energy)
                path = root / f"{sink}_profile.parquet"
                write_hourly_parquet(profile, path, timezones_path, units="p.u.")
                result = pd.read_parquet(path)
                assert str(result.index.tz) == "UTC"
                assert result.index.name == "timesteps"
                assert result.attrs["units"] == "p.u."
                assert result.attrs["end_use"] == sink
                assert result.attrs["shape_timezones"]["a"] == "Europe/Amsterdam"
                np.testing.assert_allclose(result, profile)
            combined = scaled.sum("end_use").to_series().unstack("id")
            np.testing.assert_allclose(sum(reconstructed), combined)


if __name__ == "__main__":
    unittest.main()
