"""Use weather data to simulate hourly heat profiles using When2Heat's methods.

Functions attributable to When2Heat are explicitly referenced as such in docstrings.
When2Heat can be found here: https://github.com/oruhnau/when2heat
"""

from collections.abc import Callable
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd
import xarray as xr
from _timeseries import LOCAL_TIME_BASIS
from dask import config as dask_config
from group_gridded_timeseries import group_gridcells

# ASSUME (from When2Heat):
# 1. Below 15 °C, the water heating demand is not defined and assumed to stay constant
HOT_WATER_LOWER_BOUND_TEMP = 15
# 2. the reference temperature is always 30C for hot water demand calcs
HOT_WATER_REF_TEMP = 30
# The BDEW sigmoid is not defined at or above its 40 °C pole. At these
# temperatures, total heating demand is assumed to consist only of hot water.
SPACE_HEATING_UPPER_BOUND_TEMP = 40
# All locations are separated by the average wind speed with the threshold
# 4.4 m/s separating windy and not-windy (normal) locations
AVE_WIND_SPEED_THRESHOLD = 4.4
# Keep each annual calculation chunk small enough for low-memory laptops while avoiding
# the large task graph created by the weather files' two-dimensional storage chunks.
PROFILE_SITE_CHUNK_SIZE = 512


def get_unscaled_heat_profiles(
    path_to_wind_speed: str,
    path_to_temperature: str,
    path_to_grid_weights: str,
    path_to_when2heat_daily: str,
    path_to_when2heat_hourly_com: str,
    path_to_when2heat_hourly_mfh: str,
    path_to_when2heat_hourly_sfh: str,
    weather_years: list[str | int],
    workers: int,
    out_paths: list[str],
) -> None:
    """Produce annual local-clock heat-demand profiles for arbitrary shapes.

    Profiles have correct shape and are consistent within themselves, but lack units.
    They must be later scaled so their magnitude matches annual heat demand data!

    Args:
        path_to_wind_speed (str): Gridded wind speed data in m/s.
        path_to_temperature (str): Gridded air temperature data in degrees C.
        path_to_grid_weights (str): Population weights from weather sites to shapes.
        path_to_when2heat_daily (str): When2Heat daily demand parameters.
        path_to_when2heat_hourly_com (str): Commercial hourly profile factors.
        path_to_when2heat_hourly_mfh (str): Multi-family home hourly profile factors.
        path_to_when2heat_hourly_sfh (str): Single-family home hourly profile factors.
        weather_years: Weather years used to shape profiles.
        workers (int): Number of Dask worker threads used to calculate profiles.
        out_paths: Annual checkpoint paths, in the same order as ``weather_years``.
    """
    weather_years = [int(year) for year in weather_years]
    if len(weather_years) != len(out_paths):
        raise ValueError("Each weather year must have one local-profile output path.")

    # Parameters and how to apply them is based on [@BDEW:2015]
    daily_params = read_daily_parameters(path_to_when2heat_daily)
    hourly_params = read_hourly_parameters(
        path_to_when2heat_hourly_com=path_to_when2heat_hourly_com,
        path_to_when2heat_hourly_mfh=path_to_when2heat_hourly_mfh,
        path_to_when2heat_hourly_sfh=path_to_when2heat_hourly_sfh,
    )

    # Open lazily using the files' native chunks, then reshape those chunks below for
    # the daily-to-hourly calculation.
    with (
        xr.open_dataset(
            path_to_temperature, decode_timedelta=True, chunks={}
        ) as temperature_ds,
        xr.open_dataset(
            path_to_wind_speed, decode_timedelta=True, chunks={}
        ) as wind_ds,
        xr.open_dataarray(
            path_to_grid_weights, decode_timedelta=True
        ) as grid_weights_file,
    ):
        grid_weights = grid_weights_file.load()
        population_by_site = grid_weights.fillna(0).sum("id")
        relevant_sites = population_by_site.site.where(
            population_by_site > 0, drop=True
        )
        temperature_ds = temperature_ds.sel(site=relevant_sites)
        wind_ds = wind_ds.sel(site=relevant_sites)
        temperature_ds = temperature_ds.chunk(
            {"site": PROFILE_SITE_CHUNK_SIZE, "time": -1}
        )
        wind_ds = wind_ds.chunk({"site": PROFILE_SITE_CHUNK_SIZE, "time": -1})

        # Check units
        assert temperature_ds.attrs["unit"].lower() == "degrees c"
        assert wind_ds.attrs["unit"].lower() == "m/s"

        with dask_config.set(scheduler="threads", num_workers=workers):
            for weather_year, out_path in zip(
                weather_years, out_paths, strict=True
            ):
                grouped_hourly_heat = group_gridcells(
                    _get_unscaled_heat_profile_for_weather_year(
                        temperature_ds,
                        wind_ds,
                        daily_params,
                        hourly_params,
                        weather_year,
                    ),
                    grid_weights,
                )
                grouped_hourly_heat.attrs["time_basis"] = LOCAL_TIME_BASIS
                grouped_hourly_heat.attrs["weather_year"] = weather_year
                grouped_hourly_heat.time.attrs["time_basis"] = LOCAL_TIME_BASIS
                Path(out_path).parent.mkdir(parents=True, exist_ok=True)
                grouped_hourly_heat.to_netcdf(out_path)


def _get_unscaled_heat_profile_for_weather_year(
    temperature_ds: xr.Dataset,
    wind_ds: xr.Dataset,
    daily_params: pd.DataFrame,
    hourly_params: xr.DataArray,
    weather_year: int,
) -> xr.Dataset:
    """Generate local-clock profiles with one boundary day on either side."""
    # Only need site-wide mean wind speed for this analysis.
    average_wind_speed = wind_ds["wind10m"].sel(time=str(weather_year)).mean("time")

    # Subset temperature to the weather year extended by a couple of days either end,
    # so we don't compute values for years we don't need, but keep a buffer for the
    # shifts happening when obtaining reference temperature.
    temperature_for_year = temperature_ds.sel(
        time=slice(str(weather_year - 1) + "-12-25", str(weather_year + 1) + "-01-05")
    )

    # This is a weighted average temperature from 3 days prior to each day in the
    # timeseries to represent the relative impact of historical daily temperature on
    # the heat demand of each day.
    # See [@Ruhnau:2019] for more information on the method
    reference_temperature = get_reference_temperature(
        temperature_for_year["temperature"], time_dim="time"
    )

    # UTC conversion can require the previous or following local date at a year
    # boundary. Keep one complete boundary day on either side of the target year.
    reference_temperature = reference_temperature.sel(
        time=slice(
            f"{weather_year - 1}-12-31",
            f"{weather_year + 1}-01-01",
        )
    )

    # Get daily demand
    daily_heat = when2heat_daily(
        reference_temperature, average_wind_speed, daily_params, _heat_function
    )
    daily_hot_water = when2heat_daily(
        reference_temperature, average_wind_speed, daily_params, _water_function
    )

    # Map profiles to daily demand
    hourly_heat = get_hourly_heat_profiles(
        reference_temperature, daily_heat, hourly_params
    )

    hourly_hot_water = get_hourly_heat_profiles(
        reference_temperature.clip(min=HOT_WATER_REF_TEMP),
        daily_hot_water,
        hourly_params,
    )

    # Space heating demand = total heating demand - hot water demand
    hourly_space = (hourly_heat - hourly_hot_water).clip(min=0)
    grouped_hourly_heat = xr.merge(
        [hourly_space.rename("space_heat"), hourly_hot_water.rename("hot_water")]
    )
    return grouped_hourly_heat


def get_hourly_heat_profiles(
    reference_temperature: xr.DataArray,
    daily_heat: xr.DataArray,
    hourly_params: xr.DataArray,
) -> xr.DataArray:
    """Convert daily heat demand to hourly profiles.

    Heavily modified from https://github.com/oruhnau/when2heat/blob/351bd1a2f9392ed50a7bdb732a103c9327c51846/scripts/demand.py
    to work with xarray datasets and to improve efficiency

    Args:
        reference_temperature (xr.DataArray): Daily reference temperature in degrees C.
        daily_heat (xr.DataArray): Relative daily heat demand per site.
        hourly_params (xr.DataArray): Parameters from When2Heat.

    Returns:
        xr.DataArray: Hourly heat demand profiles (must be re-scaled later).
    """
    # get temperature in 5C increments between -15C and +30C
    temperature_increments = (
        np.ceil((reference_temperature / 5).astype("float64")) * 5
    ).clip(min=-15, max=30)
    # When2Heat follows strftime('%w'): Sunday=0, ..., Saturday=6. Pandas/xarray
    # uses Monday=0, so rotate the local weekday before selecting commercial factors.
    temperature_increments.coords["weekday"] = when2heat_weekday(
        temperature_increments.time
    )
    hourly_params_at_all_locations = hourly_params.sel(
        temperature=temperature_increments, weekday=temperature_increments.weekday
    ).drop_vars(["temperature", "weekday"])

    # Get the relative heat demand per hour
    hourly_heat = hourly_params_at_all_locations * daily_heat
    hourly_heat = _hour_and_day_to_datetime(hourly_heat)

    return hourly_heat


def when2heat_weekday(time: xr.DataArray) -> xr.DataArray:
    """Return When2Heat weekday numbers: Sunday=0 through Saturday=6."""
    return (time.dt.dayofweek + 1) % 7


def read_daily_parameters(file_path: str) -> pd.DataFrame:
    """Load When2Heat daily parameters.

    Direct copy from https://github.com/oruhnau/when2heat/blob/351bd1a2f9392ed50a7bdb732a103c9327c51846/scripts/read.py
    """
    return pd.read_csv(file_path, sep=";", decimal=",", header=[0, 1], index_col=0)


def read_hourly_parameters(
    path_to_when2heat_hourly_com: str,
    path_to_when2heat_hourly_mfh: str,
    path_to_when2heat_hourly_sfh: str,
) -> xr.DataArray:
    """Load When2Heat hourly parameters.

    Modified from https://github.com/oruhnau/when2heat/blob/351bd1a2f9392ed50a7bdb732a103c9327c51846/scripts/read.py
    to set columns as integer.
    """
    parameters = {}

    parameters["COM"] = _csv_reader("COM", path_to_when2heat_hourly_com)

    for building_type, file_path in [
        ("SFH", path_to_when2heat_hourly_sfh),
        ("MFH", path_to_when2heat_hourly_mfh),
    ]:
        parameters[building_type] = (
            _csv_reader(building_type, file_path)
            .rename_axis(index="time")
            .align(parameters["COM"])[0]
        )

    # Postprocess the dataframe to make it easier to use in subsequent operations.
    combined_df = pd.concat(
        parameters.values(), keys=parameters.keys(), names=["building"]
    )
    combined_df.columns = combined_df.columns.astype(int).rename("temperature")
    combined_df = combined_df.rename(lambda x: int(x.replace(":00", "")), level="time")
    combined_df = combined_df.rename_axis(index={"time": "hour"})
    return combined_df.stack().to_xarray()


def get_reference_temperature(
    temperature: xr.DataArray, time_dim: str = "time"
) -> xr.DataArray:
    """Get daily reference temperature values.

    Values account for the temperature in preceding days using a weighted average.

    Modified from https://github.com/oruhnau/when2heat/blob/351bd1a2f9392ed50a7bdb732a103c9327c51846/scripts/demand.py
    to expect xarray not pandas

    Args:
        temperature (xr.DataArray): Hourly temperature in degrees C
        time_dim (str, optional): Name of the hourly time dimension. Defaults to "time".

    Returns:
        xr.DataArray: Daily reference temperatures per site.
    """
    # The weather producer guarantees complete, midnight-aligned hourly data, making
    # 24-hour coarsening equivalent to daily resampling with far fewer Dask tasks.
    # Keeping this in xarray also avoids the eager pandas MultiIndex memory spike.
    daily_average = (
        temperature.coarsen(
            {time_dim: 24}, boundary="exact", coord_func={time_dim: "min"}
        )
        .mean()
        .transpose(time_dim, ...)
    )

    # Weighted mean, method for which is given in [@Ruhnau:2019]
    return sum(
        (0.5**i) * daily_average.shift({time_dim: i}).bfill(time_dim) for i in range(4)
    ) / sum(0.5**i for i in range(4))


def when2heat_daily(
    temperature: xr.DataArray,
    wind: xr.DataArray,
    all_parameters: pd.DataFrame,
    func: Callable,
) -> xr.DataArray:
    """When2Heat's daily function.

    Modified from https://github.com/oruhnau/when2heat/blob/351bd1a2f9392ed50a7bdb732a103c9327c51846/scripts/demand.py
    The change was to not prescribe index level names.

    All locations are separated by the average wind speed with the threshold 4.4 m/s
    separating windy and not-windy (normal) locations, then relevant parameters for that
    windiness are applied in a function derived from [@BDEW:2015] to get
    "daily heat demand".
    """
    buildings = ["SFH", "MFH", "COM"]

    return xr.concat(
        [
            xr.where(
                wind <= AVE_WIND_SPEED_THRESHOLD,
                func(temperature, all_parameters[(building, "normal")]),
                func(temperature, all_parameters[(building, "windy")]),
            ).where(wind.notnull())
            for building in buildings
        ],
        dim=pd.Index(buildings, name="building"),
    )


def _hour_and_day_to_datetime(da: xr.DataArray) -> xr.DataArray:
    """Combine hour and date ('time') into one datetime ('time') dimension."""
    da = da.stack(new_time=["time", "hour"])
    new_time = da.new_time.to_index()
    datetime_index = new_time.get_level_values(0) + pd.to_timedelta(
        new_time.get_level_values(1), unit="h"
    )
    da = da.drop_vars(["new_time", "time", "hour"])
    da.coords["new_time"] = datetime_index
    return da.rename({"new_time": "time"})


def _csv_reader(
    building_type: Literal["SFH", "MFH", "COM"], file_path: str
) -> pd.DataFrame:
    # MultiIndex for commercial heat because of weekday dependency
    index_col = [0, 1] if building_type == "COM" else 0
    return pd.read_csv(file_path, sep=";", decimal=",", index_col=index_col).apply(
        pd.to_numeric, downcast="float"
    )


def _heat_function(temperature: xr.DataArray, parameters: pd.DataFrame) -> xr.DataArray:
    """A function for the total (space + water) daily heating demand.

    Derived from [@BDEW:2015].

    Modified from https://github.com/oruhnau/when2heat/blob/351bd1a2f9392ed50a7bdb732a103c9327c51846/scripts/demand.py
    so temperatures outside the BDEW sigmoid domain produce hot-water-only demand.

    Args:
        temperature (xr.DataArray): Reference daily temperature in degrees C.
        parameters (pd.DataFrame): Relevant parameters from When2Heat.

    Returns:
        xr.DataArray: Daily relative heat demand.
    """
    below_space_heating_bound = temperature < SPACE_HEATING_UPPER_BOUND_TEMP
    # xr.where evaluates both branches, so give the sigmoid a valid temperature even
    # where its result will be replaced by hot-water-only demand in the final result.
    temperature_for_sigmoid = xr.where(
        below_space_heating_bound, temperature, SPACE_HEATING_UPPER_BOUND_TEMP - 1
    )
    sigmoid = (
        parameters["A"]
        / (
            1
            + (
                parameters["B"]
                / (temperature_for_sigmoid - SPACE_HEATING_UPPER_BOUND_TEMP)
            )
            ** parameters["C"]
        )
        + parameters["D"]
    )

    linear = xr.concat(
        [
            parameters[f"m_{i}"] * temperature_for_sigmoid + parameters[f"b_{i}"]
            for i in ["s", "w"]
        ],
        dim="param",
    ).max("param")

    return xr.where(
        below_space_heating_bound,
        sigmoid + linear,
        _water_function(temperature, parameters),
    )


def _water_function(
    temperature: xr.DataArray, parameters: pd.DataFrame
) -> xr.DataArray:
    """A function for the daily water heating demand, derived from [@BDEW:2015].

    Direct copy from https://github.com/oruhnau/when2heat/blob/351bd1a2f9392ed50a7bdb732a103c9327c51846/scripts/demand.py

    Args:
        temperature (xr.DataArray): Reference daily temperature in degrees C.
        parameters (pd.DataFrame): Relevant parameters from When2Heat.

    Returns:
        xr.DataArray: Daily relative hot water demand.

    """
    celsius_clipped = temperature.clip(min=HOT_WATER_LOWER_BOUND_TEMP)

    return parameters["m_w"] * celsius_clipped + parameters["b_w"] + parameters["D"]


if __name__ == "__main__":
    get_unscaled_heat_profiles(
        path_to_wind_speed=snakemake.input.wind_speed,
        path_to_temperature=snakemake.input.temperature,
        path_to_grid_weights=snakemake.input.grid_weights,
        path_to_when2heat_daily=snakemake.input.when2heat_daily,
        path_to_when2heat_hourly_com=snakemake.input.when2heat_hourly_com,
        path_to_when2heat_hourly_mfh=snakemake.input.when2heat_hourly_mfh,
        path_to_when2heat_hourly_sfh=snakemake.input.when2heat_hourly_sfh,
        weather_years=snakemake.params.weather_years,
        workers=snakemake.threads,
        out_paths=list(snakemake.output.local_profiles),
    )
