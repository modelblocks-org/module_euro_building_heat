"""Prepare heat-pump COP and electricity-demand timeseries."""

import pandas as pd
import xarray as xr


def _group_end_uses(
    resolution_specific_data: xr.Dataset,
    annual_demand: xr.DataArray,
    weather_model_years: dict[int, int],
) -> xr.DataArray:
    """Combine hot water and space heating to generate weighted average COP."""
    weighted_average_da = (
        resolution_specific_data.to_array("end_use")
        .groupby("time.year")
        .apply(
            _end_use_weighted_ave,
            annual_demand=annual_demand,
            weather_model_years={
                int(k): int(v) for k, v in weather_model_years.items()
            },
        )
    )
    return weighted_average_da


def prepare_annual_demand(annual_demand: pd.DataFrame) -> xr.DataArray:
    """Restructure annual demand MultiIndex series into a multi-dimensional array.

    Result sums over all building categories and only contains hot water and space
    heating demands (without cooking).
    """
    return (
        annual_demand.rename_axis(columns="id")
        .stack()
        .unstack("end_use")
        .to_xarray()[["space_heat", "hot_water"]]
        .to_array("end_use")
        .sum("cat_name")
    )


def electricity_demand_from_heat(
    heat_demand: pd.DataFrame, cop: pd.DataFrame
) -> pd.DataFrame:
    """Convert heat demand to electricity demand using the heat pump COP."""
    cop = cop.reindex(index=heat_demand.index, columns=heat_demand.columns)
    electricity_demand = heat_demand.div(cop.where(cop > 0))
    return electricity_demand.where(heat_demand != 0, 0)


def _end_use_weighted_ave(
    one_year_profile: xr.DataArray,
    annual_demand: xr.DataArray,
    weather_model_years: dict[int, int],
) -> xr.DataArray:
    """Calculate weighted average of all heat energy end uses.

    Uses annual demands per spatial unit as the weights.
    """
    weather_year = int(one_year_profile.time.dt.year[0])
    if weather_year not in weather_model_years:
        raise ValueError(
            f"No model year mapping found for weather year {weather_year}."
        )
    model_year = weather_model_years[weather_year]

    demand = annual_demand.sel(year=model_year).drop("year")
    total_demand = demand.sum("end_use")
    normalised_demand = demand / total_demand.where(total_demand > 0)
    return (one_year_profile * normalised_demand).sum("end_use")


if __name__ == "__main__":
    timeseries_data = xr.open_dataset(
        snakemake.input.timeseries_data, decode_timedelta=True
    )
    annual_demand = pd.read_parquet(snakemake.input.annual_demand)
    annual_demand_ds = prepare_annual_demand(annual_demand)

    timeseries_data_group_end_use = _group_end_uses(
        timeseries_data, annual_demand_ds, snakemake.params.weather_model_years
    )

    final_df = (
        timeseries_data_group_end_use.astype("float32")
        .to_series()
        .unstack("id")
        .rename_axis(index="timesteps")
    )
    final_df.to_parquet(snakemake.output.cop)

    heat_demand = pd.read_parquet(snakemake.input.heat_demand)
    electricity_demand = electricity_demand_from_heat(heat_demand, final_df).astype(
        "float32"
    )
    electricity_demand.to_parquet(snakemake.output.electricity_demand)
