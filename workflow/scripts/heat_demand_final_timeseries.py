"""Scale shape-level heat demand profiles to annual demand totals."""

import numpy as np
import pandas as pd
import xarray as xr

TWH_TO_MWH = 1e6


def scale_heat_demand_profiles(
    annual_demand_twh: xr.Dataset,
    unscaled_demand_profiles: xr.Dataset,
    sfh_mfh_shares: dict,
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
        sfh_mfh_shares (dict):
            Share of single- and multi-family households, used to combine respective
            unscaled demand profiles into one "household" profile.
        weather_model_years:
            Mapping from weather years in the profile timestamps to model years in
            annual demand totals.

    Returns:
        xr.DataArray: merged and scaled heat demand profiles.
    """
    assert np.isclose(sum(sfh_mfh_shares.values()), 1), (
        "Household type (single- vs multi-family home) shares must add up to 1."
    )

    sfh_mfh_shares_da = (
        pd.Series({"COM": 1, **sfh_mfh_shares})
        .rename_axis(index="building")
        .to_xarray()
    )
    household_building_renamer = {k: "household" for k in sfh_mfh_shares}
    building_to_category = xr.DataArray(
        pd.Series({"COM": "commercial", **household_building_renamer}).rename_axis(
            index="building"
        )
    )
    grouped_unscaled_demand = (
        (unscaled_demand_profiles * sfh_mfh_shares_da)
        .assign_coords(cat_name=building_to_category)
        .groupby("cat_name")
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
    if weather_year not in weather_model_years:
        raise ValueError(
            f"No model year mapping found for weather year {weather_year}."
        )
    model_year = weather_model_years[weather_year]
    normalised_profile = one_year_profile / one_year_profile.sum("time")
    demand = normalised_profile * annual_demand.sel(year=model_year).drop("year")
    return demand.sum("cat_name")


def prepare_annual_demand(annual_demand: pd.Series) -> xr.DataArray:
    """Restructure annual demand MultiIndex series into a multi-dimensional array.

    Result sums over all building categories and only contains hot water and space
    heating demands (not cooking).
    """
    return (
        annual_demand.rename_axis(columns="id")
        .stack()
        .unstack("end_use")
        .to_xarray()[["space_heat", "hot_water"]]
    )


if __name__ == "__main__":
    annual_demand = pd.read_parquet(snakemake.input.annual_demand)
    annual_demand_ds = prepare_annual_demand(annual_demand)
    unscaled_profiles = xr.open_dataset(
        snakemake.input.timeseries_data, decode_timedelta=True
    )
    scaled_profiles = scale_heat_demand_profiles(
        annual_demand_ds,
        unscaled_profiles,
        snakemake.params.sfh_mfh_shares,
        snakemake.params.weather_model_years,
    )

    final_df = (
        scaled_profiles.sum("end_use")
        .astype("float32")
        .to_series()
        .unstack("id")
        .rename_axis(index="timesteps")
    )
    final_df.to_parquet(snakemake.output[0])
