"""Prepare heat-pump COP and electricity-demand timeseries."""

import sys
from typing import TYPE_CHECKING, Any

import pandas as pd
import xarray as xr
from _timeseries import utc_aware_hourly_frame, write_hourly_parquet

if TYPE_CHECKING:
    snakemake: Any


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
    if not heat_demand.index.equals(cop.index):
        raise ValueError(
            "Heat-demand and COP UTC indices must be exactly equal before combining."
        )
    missing_columns = sorted(set(heat_demand.columns) - set(cop.columns))
    extra_columns = sorted(set(cop.columns) - set(heat_demand.columns))
    if missing_columns or extra_columns:
        raise ValueError(
            "Heat-demand and COP shape columns differ: "
            f"missing COP columns={missing_columns}, extra COP columns={extra_columns}."
        )
    cop = cop.reindex(columns=heat_demand.columns)
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

    demand = annual_demand.sel(year=model_year, drop=True)
    total_demand = demand.sum("end_use")
    normalised_demand = demand / total_demand.where(total_demand > 0)
    return (one_year_profile * normalised_demand).sum("end_use")


def main() -> None:
    """Main Snakemake process."""
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
    final_df = utc_aware_hourly_frame(final_df)

    heat_demand = pd.read_parquet(snakemake.input.heat_demand)
    if not heat_demand.index.equals(final_df.index):
        raise ValueError(
            "Heat-demand and COP UTC indices must be exactly equal before combining."
        )
    electricity_demand = electricity_demand_from_heat(heat_demand, final_df).astype(
        "float32"
    )
    write_hourly_parquet(
        final_df, snakemake.output.cop, snakemake.input.shape_timezones
    )
    write_hourly_parquet(
        electricity_demand,
        snakemake.output.electricity_demand,
        snakemake.input.shape_timezones,
    )


if __name__ == "__main__":
    sys.stderr = open(snakemake.log[0], "w", buffering=1)
