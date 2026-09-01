"""Calculate heat-pump coefficient of performance from weather data."""

import math
import sys
from typing import TYPE_CHECKING, Any

import pandas as pd
import xarray as xr

if TYPE_CHECKING:
    snakemake: Any


def cop(
    path_to_temperature_air: str,
    path_to_temperature_ground: str,
    path_to_heat_pump_characteristics: str,
    sink_temperature: dict[str, int],
    space_heat_sink_shares: dict[str, float],
    correction_factor: float,
    heat_pump_shares: dict[str, float],
    weather_years: list[int],
    path_to_output: str,
) -> None:
    """Calculate heat pump Coefficient of Performance (COP) from manufacturer data.

    COP is calculated for air-source and ground-source heat pumps according to
    different source temperature data.

    Args:
        path_to_temperature_air (str):
            Gridded air temperature timeseries data.
        path_to_temperature_ground (str):
            Gridded ground/soil temperature timeseries data.
        path_to_heat_pump_characteristics (str):
            Manufacturer data on heat pump characteristics across a product range.
        sink_temperature (dict[str, int]):
            Working temperature for different heating methods
            ('sink' temp. of a heat pump).
        space_heat_sink_shares (dict[str, float]):
            Share of different space heating methods assumed for the building stock.
        correction_factor (float):
            Factor with which to downrate heat pump performance to go from manufacturer
            data to "operational" performance.
        heat_pump_shares (dict[str, float]):
            Share of air- vs ground-source heat pumps in the market.
        weather_years (list[int]):
            Weather years to include in the profile.
        path_to_output (str):
            Output to which COP timeseries data will be saved.
    """
    # Initial fast-fail checks.
    assert math.isclose(sum(heat_pump_shares.values()), 1), (
        "Heat pump technology shares must add up to 1."
    )
    assert math.isclose(sum(space_heat_sink_shares.values()), 1), (
        "Space-heating sink method shares must add up to 1."
    )

    hot_water_methods = set(sink_temperature) - set(space_heat_sink_shares)
    if len(hot_water_methods) != 1:
        raise ValueError(
            "`heat.heat_pump.sink_temperature` must define exactly one hot-water "
            "sink method not listed in `space_heat_sink_shares`."
        )
    hot_water_method = hot_water_methods.pop()

    # 1. Load the gridded source temperatures.
    temperature_ds = xr.merge(
        _load_temperature_data(filepath, weather_years)
        for filepath in [path_to_temperature_air, path_to_temperature_ground]
    )

    # 2. Get characteristics per sink method.
    pre_grouped_heat_pump_characteristics = (
        xr.open_dataarray(path_to_heat_pump_characteristics, decode_timedelta=True)
        .mean("product")  # ASSUME: take the average of all heat pump products
        .sel(data_type="COP")
        .interp(sink_temp=list(sink_temperature.values()))
        .assign_coords(sink_temp=list(sink_temperature.keys()))
    )
    # 3. Combine sink methods into space heating and hot water end uses,
    # using weightings for space heating (hot water is a distinct sink method already).
    heat_pump_characteristics = _group_sink_methods(
        pre_grouped_heat_pump_characteristics, hot_water_method, space_heat_sink_shares
    )

    cop_ashp = temperature_to_cop(
        heat_pump_characteristics.sel(source="air"),
        temperature_ds["temperature"],
        correction_factor,
    )

    cop_gshp = temperature_to_cop(
        heat_pump_characteristics.sel(source="ground"),
        # ASSUME: 5C decrease to account for soil to brine heat transfer.
        temperature_ds["tsoil5"] - 5,
        correction_factor,
    )

    combined_cop = (
        cop_ashp * heat_pump_shares["ashp"] + cop_gshp * heat_pump_shares["gshp"]
    )
    # We infill with ASHP COP for gridcells that have no GSHP data.
    # These tend to be gridcells covering areas with no/limited land.
    combined_cop = combined_cop.fillna(cop_ashp)

    cop_ds = combined_cop.to_dataset(dim="end_use")
    cop_ds.attrs["timezone"] = "UTC"
    cop_ds.time.attrs["timezone"] = "UTC"
    encoding = {k: {"zlib": True, "complevel": 4} for k in cop_ds.data_vars}
    cop_ds.to_netcdf(path_to_output, encoding=encoding)


def _group_sink_methods(
    heat_pump_characteristics: xr.DataArray,
    hot_water_method: str,
    space_heat_sink_shares: dict[str, float],
) -> xr.DataArray:
    """Group sink methods into hot-water and space-heat end uses."""
    sink_method_shares = (
        pd.Series({hot_water_method: 1, **space_heat_sink_shares})
        .rename_axis(index="sink_temp")
        .to_xarray()
    )
    end_use = pd.Series(
        {hot_water_method: "hot_water"}
        | {method: "space_heat" for method in space_heat_sink_shares},
        name="end_use",
    )
    end_use_da = xr.DataArray(
        end_use.reindex(heat_pump_characteristics.sink_temp.values).to_numpy(),
        coords={"sink_temp": heat_pump_characteristics.sink_temp},
        dims="sink_temp",
    )
    return (
        (heat_pump_characteristics * sink_method_shares)
        .assign_coords(end_use=end_use_da)
        .groupby("end_use")
        .sum("sink_temp")
    )


def temperature_to_cop(
    heat_pump_characteristics: xr.DataArray,
    temperature_celsius: xr.DataArray,
    correction_factor: float,
) -> xr.DataArray:
    """Interpolate heat-pump temperature-COP curves to weather temperatures."""
    source_cop = correction_factor * heat_pump_characteristics.dropna("source_temp")
    return source_cop.interp(
        {"source_temp": temperature_celsius}, kwargs={"fill_value": "extrapolate"}
    )


def _load_temperature_data(
    path_to_temperature_data: str, weather_years: list[int]
) -> xr.Dataset:
    """Subset to the configured weather years and check that units are correct."""
    ds = xr.open_dataset(path_to_temperature_data, decode_timedelta=True).sel(
        time=slice(str(min(weather_years)), str(max(weather_years)))
    )
    assert ds.attrs["unit"].lower() == "degrees c"
    return ds


if __name__ == "__main__":
    sys.stderr = open(snakemake.log[0], "w", buffering=1)
    cop(
        path_to_temperature_air=snakemake.input.temperature_air,
        path_to_temperature_ground=snakemake.input.temperature_ground,
        path_to_heat_pump_characteristics=snakemake.input.heat_pump_characteristics,
        sink_temperature=snakemake.params.sink_temperature,
        space_heat_sink_shares=snakemake.params.space_heat_sink_shares,
        correction_factor=snakemake.params.correction_factor,
        heat_pump_shares=snakemake.params.heat_pump_shares,
        weather_years=snakemake.params.weather_years,
        path_to_output=snakemake.output[0],
    )
