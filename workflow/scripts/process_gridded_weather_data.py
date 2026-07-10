"""Process monthly ERA5 NetCDF files into module weather files."""

from datetime import datetime
from pathlib import Path

import numpy as np
import xarray as xr

GRID_DEGREES = 0.5
KELVIN_TO_CELSIUS = 273.15

VARIABLE_ALIASES = {
    "temperature": ("t2m", "2t", "2m_temperature"),
    "u10": ("u10", "10u", "10m_u_component_of_wind"),
    "v10": ("v10", "10v", "10m_v_component_of_wind"),
    "tsoil5": ("stl1", "soil_temperature_level_1"),
}


def weather_time_range(weather_years: list[int]) -> tuple[datetime, datetime]:
    """Return the timestamp range needed by the heat profile calculation."""
    start_year = min(weather_years)
    end_year = max(weather_years)
    return (datetime(start_year - 1, 12, 25, 0), datetime(end_year + 1, 1, 5, 23))


def _data_var(dataset: xr.Dataset, aliases: tuple[str, ...]) -> xr.DataArray:
    return next(dataset[alias] for alias in aliases if alias in dataset.data_vars)


def normalise_era5_dataset(dataset: xr.Dataset) -> xr.Dataset:
    """Normalise ERA5 coordinate names to time, lat, and lon."""
    rename = {
        name: target
        for name, target in {
            "valid_time": "time",
            "latitude": "lat",
            "longitude": "lon",
        }.items()
        if (name in dataset.coords or name in dataset.dims)
        and target not in dataset.coords
        and target not in dataset.dims
    }
    if rename:
        dataset = dataset.rename(rename)

    if float(dataset["lon"].max()) > 180:
        dataset = dataset.assign_coords(lon=((dataset["lon"] + 180) % 360) - 180)
    return dataset.sortby("lat").sortby("lon").sortby("time")


def flatten_dataarray(field: xr.DataArray, variable_name: str) -> xr.Dataset:
    """Flatten a lat/lon weather field to the module's site/time schema."""
    stacked = field.stack(site=("lat", "lon")).transpose("site", "time")
    lat = stacked["lat"].values.astype(float)
    lon = stacked["lon"].values.astype(float)
    site = np.arange(stacked.sizes["site"], dtype=np.int32)
    values = stacked.reset_index("site", drop=True).assign_coords(site=site)
    return xr.Dataset(
        {
            variable_name: values,
            "site_id": ("site", site),
            "lat": ("site", lat),
            "lon": ("site", lon),
        },
        coords={"site": site, "time": field["time"].values},
    )


def convert_era5_to_module_datasets(dataset: xr.Dataset) -> dict[str, xr.Dataset]:
    """Convert ERA5 variables to grid, temperature, wind10m, and tsoil5 datasets."""
    dataset = normalise_era5_dataset(dataset)
    temperature = (
        _data_var(dataset, VARIABLE_ALIASES["temperature"]).squeeze(drop=True)
        - KELVIN_TO_CELSIUS
    )
    tsoil5 = (
        _data_var(dataset, VARIABLE_ALIASES["tsoil5"]).squeeze(drop=True)
        - KELVIN_TO_CELSIUS
    )
    wind10m = np.hypot(
        _data_var(dataset, VARIABLE_ALIASES["u10"]).squeeze(drop=True),
        _data_var(dataset, VARIABLE_ALIASES["v10"]).squeeze(drop=True),
    )

    outputs = {
        "temperature": flatten_dataarray(
            temperature.rename("temperature"), "temperature"
        ),
        "wind10m": flatten_dataarray(wind10m.rename("wind10m"), "wind10m"),
        "tsoil5": flatten_dataarray(tsoil5.rename("tsoil5"), "tsoil5"),
    }
    outputs["temperature"].attrs["unit"] = "degrees C"
    outputs["wind10m"].attrs["unit"] = "m/s"
    outputs["tsoil5"].attrs["unit"] = "degrees C"
    outputs["grid"] = xr.Dataset(
        {
            "site_id": outputs["temperature"]["site_id"],
            "lat": outputs["temperature"]["lat"],
            "lon": outputs["temperature"]["lon"],
        },
        coords={"site": outputs["temperature"]["site"]},
    )
    return outputs


def write_module_weather_outputs(
    outputs: dict[str, xr.Dataset], output_paths: dict[str, str]
) -> None:
    """Write module weather datasets with compression and atomic renames."""
    for name, dataset in outputs.items():
        output_path = Path(output_paths[name])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
        encoding = {
            variable: {"zlib": True, "complevel": 4, "dtype": "float32"}
            for variable in dataset.data_vars
            if variable not in {"site_id", "lat", "lon"}
        }
        dataset.to_netcdf(tmp_path, encoding=encoding)
        tmp_path.replace(output_path)


def process_gridded_weather_data(
    era5_files: list[str | Path], output_paths: dict[str, str], weather_years: list[int]
) -> None:
    """Combine monthly ERA5 files into weather files expected by the workflow."""
    start, end = weather_time_range(weather_years)
    with xr.open_mfdataset(
        era5_files,
        combine="nested",
        concat_dim="time",
        data_vars="minimal",
        coords="minimal",
        compat="override",
        join="exact",
        combine_attrs="override",
        decode_timedelta=True,
    ) as monthly_era5:
        selected_era5 = monthly_era5.sel(time=slice(start, end))
        outputs = convert_era5_to_module_datasets(selected_era5)
        for dataset in outputs.values():
            dataset.attrs.update(
                {
                    "dataset": "era5",
                    "date_from": start.strftime("%Y-%m-%d"),
                    "date_to": end.strftime("%Y-%m-%d"),
                    "grid_degrees": GRID_DEGREES,
                }
            )
        write_module_weather_outputs(outputs, output_paths)


if __name__ == "__main__":
    process_gridded_weather_data(
        era5_files=list(snakemake.input.era5_heat),
        output_paths={
            "grid": snakemake.output.grid,
            "temperature": snakemake.output.temperature,
            "wind10m": snakemake.output.wind10m,
            "tsoil5": snakemake.output.tsoil5,
        },
        weather_years=[int(year) for year in snakemake.params.weather_years],
    )
