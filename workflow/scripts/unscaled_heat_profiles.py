"""Use weather data to simulate hourly heat profiles using When2Heat's methods.

Functions attributable to When2Heat are explicitly referenced as such in docstrings.
When2Heat can be found here: https://github.com/oruhnau/when2heat
"""

from collections.abc import Callable
from typing import Literal

import numpy as np
import pandas as pd
import xarray as xr
from dask import config as dask_config

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
# Keep each annual output chunk small enough for low-memory laptops while avoiding
# the large task graph created by the weather files' two-dimensional storage chunks.
PROFILE_SITE_CHUNK_SIZE = 512
PROFILE_WORKERS = 2


def get_unscaled_heat_profiles(
    path_to_wind_speed: str,
    path_to_temperature: str,
    path_to_when2heat_daily: str,
    path_to_when2heat_hourly_com: str,
    path_to_when2heat_hourly_mfh: str,
    path_to_when2heat_hourly_sfh: str,
    weather_years: list[str | int],
    out_path: str,
) -> None:
    """Produces time series of heat demand profiles.

    Profiles have correct shape and are consistent within themselves, but lack units.
    They must be later scaled so their magnitude matches annual heat demand data!

    Args:
        path_to_wind_speed (str): Gridded wind speed data in m/s.
        path_to_temperature (str): Gridded air temperature data in degrees C.
        path_to_when2heat_daily (str): When2Heat daily demand parameters.
        path_to_when2heat_hourly_com (str): Commercial hourly profile factors.
        path_to_when2heat_hourly_mfh (str): Multi-family home hourly profile factors.
        path_to_when2heat_hourly_sfh (str): Single-family home hourly profile factors.
        weather_years: Weather years used to shape profiles.
        out_path (str): Path to which data will be saved.
    """
    weather_years = [int(year) for year in weather_years]

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
    ):
        temperature_ds = temperature_ds.chunk(
            {"site": PROFILE_SITE_CHUNK_SIZE, "time": -1}
        )
        wind_ds = wind_ds.chunk({"site": PROFILE_SITE_CHUNK_SIZE, "time": -1})

        # Check units
        assert temperature_ds.attrs["unit"].lower() == "degrees c"
        assert wind_ds.attrs["unit"].lower() == "m/s"

        grouped_hourly_heat = xr.concat(
            [
                _get_unscaled_heat_profile_for_weather_year(
                    temperature_ds, wind_ds, daily_params, hourly_params, weather_year
                )
                for weather_year in weather_years
            ],
            dim="time",
        ).sortby("time")
        encoding = {
            k: {"zlib": True, "complevel": 4} for k in grouped_hourly_heat.data_vars
        }
        # NetCDF writes are serialized internally, so a small worker pool provides
        # useful compute overlap without multiplying the per-chunk memory footprint.
        with dask_config.set(scheduler="threads", num_workers=PROFILE_WORKERS):
            grouped_hourly_heat.to_netcdf(out_path, encoding=encoding)


def _get_unscaled_heat_profile_for_weather_year(
    temperature_ds: xr.Dataset,
    wind_ds: xr.Dataset,
    daily_params: pd.DataFrame,
    hourly_params: xr.DataArray,
    weather_year: int,
) -> xr.Dataset:
    """Generate unscaled hourly heat profiles for one weather year."""
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

    # Subset to get only the target weather year. Output timestamps intentionally
    # remain in weather years; model years are only used later for annual scaling.
    reference_temperature = reference_temperature.sel(time=str(weather_year))

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
       hourly_params: xr.DataArray: Parameters from When2Heat.

    Returns:
        xr.DataArray: Hourly heat demand profiles (must be re-scaled later).
    """
    # get temperature in 5C increments between -15C and +30C
    temperature_increments = (
        np.ceil((reference_temperature / 5).astype("float64")) * 5
    ).clip(min=-15, max=30)
    # Profiles are linked to the day's temperature increment, so we align the two
    temperature_increments.coords["weekday"] = temperature_increments.time.dt.dayofweek
    hourly_params_at_all_locations = hourly_params.sel(
        temperature=temperature_increments, weekday=temperature_increments.weekday
    ).drop_vars(["temperature", "weekday"])

    # Get the relative heat demand per hour
    hourly_heat = hourly_params_at_all_locations * daily_heat
    hourly_heat = _hour_and_day_to_datetime(hourly_heat)

    return hourly_heat


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
        path_to_when2heat_daily=snakemake.input.when2heat_daily,
        path_to_when2heat_hourly_com=snakemake.input.when2heat_hourly_com,
        path_to_when2heat_hourly_mfh=snakemake.input.when2heat_hourly_mfh,
        path_to_when2heat_hourly_sfh=snakemake.input.when2heat_hourly_sfh,
        weather_years=snakemake.params.weather_years,
        out_path=snakemake.output[0],
    )
