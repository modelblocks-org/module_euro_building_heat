"""Scale shape-level heat demand profiles to annual demand totals."""

import sys
from typing import TYPE_CHECKING, Any

import _plots
import pandas as pd
import xarray as xr
from _timeseries import write_hourly_parquet

if TYPE_CHECKING:
    snakemake: Any

TWH_TO_MWH = 1e6


def read_building_shares_by_shape(
    shares_path: str, shapes_path: str, shape_ids: pd.Index
) -> xr.DataArray:
    """Map national SFH/MFH shares to the shapes in an unscaled profile."""
    shares = pd.read_parquet(shares_path)

    shapes = pd.read_parquet(shapes_path, columns=["shape_id", "country_id"])
    shape_to_country = shapes.set_index("shape_id")["country_id"]

    shape_ids = pd.Index(shape_ids, name="id")
    shape_to_country = shape_to_country.reindex(shape_ids)

    shares_by_shape = pd.DataFrame(
        {
            "COM": 1.0,
            "SFH": shape_to_country.map(shares["SFH"]),
            "MFH": shape_to_country.map(shares["MFH"]),
        },
        index=shape_ids,
    )
    return xr.DataArray(
        shares_by_shape.T,
        dims=("building", "id"),
        coords={"building": shares_by_shape.columns, "id": shape_ids},
    )


def scale_heat_demand_profiles(
    annual_demand_twh: xr.Dataset,
    unscaled_demand_profiles: xr.Dataset,
    building_shares: xr.DataArray,
    weather_model_years: dict[int, int],
) -> xr.DataArray:
    """Create demand timeseries for space heat and hot water across all building types.

    ASSUME: if calculating historic electrical heating requirements, annual electricity
    demand is distributed using the demand profile.
    It therefore ignores heat pump COP profiles and the possibility of a storage buffer.

    Args:
        annual_demand_twh (xr.Dataset):
            Annual heat demand in TWh by year, resolution ID, end-use, and building
            category.
        unscaled_demand_profiles (xr.Dataset):
            Hourly heat demand profiles per year by resolution ID, end-use, and building
            category. Profiles will be normalised before scaling with annual demand, so
            their absolute magnitudes will be ignored.
        building_shares:
            Single- and multi-family dwelling shares by shape, plus a commercial
            multiplier of one, used to combine profiles into building categories.
        weather_model_years:
            Mapping from weather years in the profile timestamps to model years in
            annual demand totals.

    Returns:
        xr.DataArray: merged and scaled heat demand profiles.
    """
    building_to_category = xr.DataArray(
        pd.Series(
            {"COM": "commercial", "SFH": "household", "MFH": "household"}
        ).rename_axis(index="building")
    )
    grouped_unscaled_demand = (
        (unscaled_demand_profiles * building_shares)
        .assign_coords(category=building_to_category)
        .groupby("category")
        .sum("building")
    )
    scaled_demand_profiles = grouped_unscaled_demand.groupby("time.year").apply(
        _scale_demand,
        annual_demand=annual_demand_twh,
        weather_model_years={int(k): int(v) for k, v in weather_model_years.items()},
    )

    return scaled_demand_profiles.to_array("end_use") * TWH_TO_MWH


def _scale_demand(
    one_year_profile: xr.Dataset,
    annual_demand: xr.Dataset,
    weather_model_years: dict[int, int],
) -> xr.Dataset:
    """Scale one weather year with the annual demand for its paired model year."""
    weather_year = int(one_year_profile.time.dt.year[0])
    model_year = weather_model_years[weather_year]
    normalised_profile = one_year_profile / one_year_profile.sum("time")
    demand = normalised_profile * annual_demand.sel(year=model_year, drop=True)
    return demand.sum("category")


def prepare_annual_demand(annual_demand: pd.DataFrame) -> xr.Dataset:
    """Restructure tidy annual demand into a multi-dimensional array.

    Result sums over all building categories and only contains hot water and space
    heating demands (not cooking).
    """
    return (
        annual_demand.set_index(["end_use", "category", "year", "shape_id"])[
            "annual_heat_demand_twh"
        ]
        .to_xarray()
        .rename(shape_id="id")
        .to_dataset(dim="end_use")[["space_heat", "hot_water"]]
    )


def main() -> None:
    """Main Snakemake process."""
    annual_demand = pd.read_parquet(snakemake.input.annual_demand)
    annual_demand_ds = prepare_annual_demand(annual_demand)
    unscaled_profiles = xr.open_dataset(
        snakemake.input.timeseries_data, decode_timedelta=True
    )
    building_shares = read_building_shares_by_shape(
        snakemake.input.sfh_mfh_shares,
        snakemake.input.shapes,
        pd.Index(unscaled_profiles.id.values),
    )
    scaled_profiles = scale_heat_demand_profiles(
        annual_demand_ds,
        unscaled_profiles,
        building_shares,
        snakemake.params.weather_model_years,
    )

    final_df = (
        scaled_profiles.sum("end_use")
        .astype("float32")
        .to_series()
        .unstack("id")
        .rename_axis(index="timesteps")
    )
    final_df = write_hourly_parquet(
        final_df, snakemake.output.timeseries, snakemake.input.shape_timezones
    )
    _plots.plot_heat_demand_timeseries(final_df, snakemake.output.plot)


if __name__ == "__main__":
    sys.stderr = open(snakemake.log[0], "w", buffering=1)
    main()
