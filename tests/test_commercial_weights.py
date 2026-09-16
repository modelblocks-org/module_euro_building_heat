"""Synthetic checks for sector-specific annual and hourly allocation."""

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

from _schemas import validate_residential_space_heat_weight_raster  # noqa: E402
from aggregate_residential_space_heat_weight import aggregate_support  # noqa: E402
from group_gridded_timeseries import group_gridcells  # noqa: E402
from rescale_annual_heat_demand import (  # noqa: E402
    climate_adjusted_weights,
    rescale_to_shapes,
)


class CommercialWeightsTest(unittest.TestCase):
    """Keep national totals while using independent sector support."""

    def test_sector_raster_units_and_aggregation(self):
        """Accept the producer's distinct sector units and reject incorrect units."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "support.tif"
            polygons = gpd.GeoDataFrame(geometry=[box(0, 0, 200, 100)], crs="EPSG:3035")
            for sector, units in [
                ("residential", "weighted_m2/ha"),
                ("commercial", "m2/ha"),
            ]:
                with rasterio.open(
                    path,
                    "w",
                    driver="GTiff",
                    width=2,
                    height=1,
                    count=1,
                    dtype="float64",
                    crs="EPSG:3035",
                    transform=from_origin(0, 100, 100, 100),
                    nodata=0,
                ) as output:
                    output.write(np.array([[2.0, 3.0]]), 1)
                    output.set_band_description(1, f"{sector}_space_heat_weight")
                    output.set_band_unit(1, units)
                validate_residential_space_heat_weight_raster(path, sector=sector)
                np.testing.assert_allclose(
                    aggregate_support(path, polygons, sector), [5]
                )
                with rasterio.open(path, "r+") as output:
                    output.set_band_unit(1, "MWh")
                with self.assertRaisesRegex(ValueError, "must use units"):  # noqa: PT027
                    validate_residential_space_heat_weight_raster(path, sector=sector)

    def test_annual_allocation_and_zero_support(self):
        """Use raw sector support for hot water and HDD support for space heat."""
        structural = xr.DataArray(
            [[1, 0], [0, 3]],
            dims=["site", "id"],
            coords={"site": [0, 1], "id": ["a", "b"]},
        )
        hdd = xr.DataArray(
            [[1, 4]],
            dims=["weather_year", "site"],
            coords={"weather_year": [2023], "site": [0, 1]},
        )
        weights, _ = climate_adjusted_weights(structural, hdd, {2023: 2020}, 0.5)
        national = pd.DataFrame(
            {"NLD": [70.0, 40.0, 30.0, 20.0, 8.0]},
            index=pd.MultiIndex.from_tuples(
                [
                    ("space_heat", "commercial", 2020),
                    ("space_heat", "household", 2020),
                    ("hot_water", "commercial", 2020),
                    ("hot_water", "household", 2020),
                    ("cooking", "household", 2020),
                ],
                names=["end_use", "cat_name", "year"],
            ),
        )
        mapping = pd.Series({"a": "NLD", "b": "NLD"})
        population = pd.Series({"a": 3.0, "b": 1.0})
        household = pd.DataFrame({2020: [1.0, 1.0]}, index=["a", "b"])
        residential_raw = pd.Series({"a": 2.0, "b": 3.0})
        commercial_raw = structural.sum("site").to_series()
        result = rescale_to_shapes(
            national,
            mapping,
            population,
            household,
            weights,
            residential_raw,
            commercial_raw,
        )
        np.testing.assert_allclose(
            result.to_numpy(), [[10, 60], [20, 20], [7.5, 22.5], [8, 12], [6, 2]]
        )
        np.testing.assert_allclose(result.sum(axis=1), national.NLD)
        with self.assertRaisesRegex(ValueError, "zero HDD-adjusted commercial support"):  # noqa: PT027
            rescale_to_shapes(
                national,
                mapping,
                population,
                household,
                weights * 0,
                residential_raw,
                commercial_raw,
            )
        changed_weights, _ = climate_adjusted_weights(
            structural, hdd * xr.DataArray([9, 1], dims="site"), {2023: 2020}, 0.5
        )
        changed = rescale_to_shapes(
            national,
            mapping,
            population,
            household,
            changed_weights,
            residential_raw,
            commercial_raw,
        )
        np.testing.assert_allclose(changed.loc["hot_water"], result.loc["hot_water"])
        assert not np.allclose(changed.loc["space_heat"], result.loc["space_heat"])

    def test_hourly_sector_weights(self):
        """COM follows commercial support, SFH residential, hot water population."""
        values = np.array([[10.0, 10.0], [30.0, 30.0]])
        data = xr.Dataset(
            {
                name: (("site", "building"), values)
                for name in ["space_heat", "hot_water"]
            },
            coords={"site": [0, 1], "building": ["COM", "SFH"]},
        )
        population = xr.DataArray(
            [[1.0, 1.0], [1.0, 1.0]],
            dims=["site", "id"],
            coords={"site": [0, 1], "id": ["a", "b"]},
        )
        residential = population * xr.DataArray([1, 0], dims="site")
        commercial = population * xr.DataArray([[0, 0], [1, 0]], dims=["site", "id"])
        result = group_gridcells(data, population, residential, commercial)
        np.testing.assert_allclose(result.space_heat.sel(building="COM"), [30, 20])
        np.testing.assert_allclose(result.space_heat.sel(building="SFH"), [10, 10])
        np.testing.assert_allclose(result.hot_water, 20)


if __name__ == "__main__":
    unittest.main()
