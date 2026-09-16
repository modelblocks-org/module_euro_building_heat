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
        """Combine household heat and coarse population on the original grid."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            household_path = root / "household.tif"
            population_path = root / "population.tif"
            household = np.array([[10, 0, 5, 20, 5, 5]] * 2, dtype=float)
            transform = from_origin(0, 200, 100, 100)
            with rasterio.open(
                household_path,
                "w",
                driver="GTiff",
                width=6,
                height=2,
                count=2,
                dtype="float64",
                crs="EPSG:3035",
                transform=transform,
                nodata=0,
            ) as output:
                for band, year in enumerate([2020, 2021], start=1):
                    output.write(household * band, band)
                    output.update_tags(band, demand_year=year, weather_year=year + 3)
            with rasterio.open(
                population_path,
                "w",
                driver="GTiff",
                width=3,
                height=1,
                count=1,
                dtype="float64",
                crs="EPSG:3035",
                transform=from_origin(0, 200, 200, 200),
                nodata=-9999,
            ) as output:
                output.write(np.array([[1, 3, 2]], dtype=float), 1)
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
                    records.append(["cooking", "household", year, shape_id, 999])
            annual = pd.DataFrame(
                records,
                columns=["end_use", "category", "year", "shape_id", "heat_demand_twh"],
            )
            paths = {
                sink: root / f"{sink}_demand_mwh.tif"
                for sink in ["space_heat", "hot_water"]
            }
            write_heat_demand_rasters(
                annual, shapes, household_path, population_path, paths
            )
            expected = {
                "space_heat": household + np.array([[2, 2, 6, 6, 4, 4]] * 2),
                "hot_water": np.array([[5, 5, 15, 15, 10, 10]] * 2),
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
            # Upstream weather/shape intersections can assign boundary cells
            # differently from the final shape rasterization. Keep the local
            # household pattern while restoring each shape's annual total.
            with rasterio.open(household_path, "r+") as output:
                for band in output.indexes:
                    shifted = household * band
                    shifted[:, :3] *= 1.1 * band
                    shifted[:, 3:] *= 0.9 / band
                    output.write(shifted, band)
            write_heat_demand_rasters(
                annual, shapes, household_path, population_path, paths
            )
            for sink, path in paths.items():
                with rasterio.open(path) as output:
                    for band in output.indexes:
                        np.testing.assert_allclose(
                            output.read(band), expected[sink] * band
                        )
            with rasterio.open(household_path, "r+") as output:
                output.write(np.zeros_like(household), 1)
            with self.assertRaisesRegex(ValueError, "No household space heat support"):  # noqa: PT027
                write_heat_demand_rasters(
                    annual, shapes, household_path, population_path, paths
                )
            with rasterio.open(household_path, "r+") as output:
                output.write(household, 1)
            with rasterio.open(population_path, "r+") as output:
                output.write(np.zeros((1, 3)), 1)
            with self.assertRaisesRegex(ValueError, "No population support"):  # noqa: PT027 -- runs in module env without pytest
                write_heat_demand_rasters(
                    annual, shapes, household_path, population_path, paths
                )

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
