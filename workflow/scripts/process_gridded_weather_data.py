"""Process a local ERA5 NetCDF file into module weather files."""

import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import xarray as xr

GRID_DEGREES = 0.25
KELVIN_TO_CELSIUS = 273.15


def weather_time_range(weather_years: list[int]) -> tuple[datetime, datetime]:
    """Return the timestamp range needed by the heat profile calculation."""
    return (
        datetime(min(weather_years) - 1, 12, 25),
        datetime(max(weather_years) + 1, 1, 5, 23),
    )


def flatten_dataarray(field: xr.DataArray, variable_name: str) -> xr.Dataset:
    """Flatten a lat/lon weather field to the module's site/time schema."""
    stacked = field.stack(site=("lat", "lon")).transpose("site", "time")
    site = np.arange(stacked.sizes["site"], dtype=np.int32)
    values = stacked.reset_index("site", drop=True).assign_coords(site=site)
    return xr.Dataset(
        {
            variable_name: values,
            "site_id": ("site", site),
            "lat": ("site", stacked.lat.values.astype(float)),
            "lon": ("site", stacked.lon.values.astype(float)),
        },
        coords={"site": site, "time": field.time.values},
    )


def convert_era5_to_module_datasets(dataset: xr.Dataset) -> dict[str, xr.Dataset]:
    """Convert ERA5 variables to grid, temperature, wind10m, and tsoil5 datasets."""
    fields = {
        "temperature": dataset.t2m.squeeze(drop=True) - KELVIN_TO_CELSIUS,
        "wind10m": np.hypot(
            dataset.u10.squeeze(drop=True), dataset.v10.squeeze(drop=True)
        ),
        "tsoil5": dataset.stl1.squeeze(drop=True) - KELVIN_TO_CELSIUS,
    }
    outputs = {
        name: flatten_dataarray(field.rename(name), name)
        for name, field in fields.items()
    }
    outputs["temperature"].attrs["unit"] = "degrees C"
    outputs["wind10m"].attrs["unit"] = "m/s"
    outputs["tsoil5"].attrs["unit"] = "degrees C"
    outputs["grid"] = xr.Dataset(
        {name: outputs["temperature"][name] for name in ("site_id", "lat", "lon")},
        coords={"site": outputs["temperature"].site},
    )
    for dataset in outputs.values():
        if "time" in dataset.coords:
            dataset.time.attrs["timezone"] = "UTC"
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
    path_to_era5: str | Path, output_paths: dict[str, str], weather_years: list[int]
) -> None:
    """Convert a local ERA5 file to the weather files used by the workflow."""
    start, end = weather_time_range(weather_years)
    with xr.open_dataset(path_to_era5, chunks={}) as era5:
        outputs = convert_era5_to_module_datasets(era5)
        for dataset in outputs.values():
            dataset.attrs.update(
                {
                    "dataset": "era5-edh",
                    "date_from": start.strftime("%Y-%m-%d"),
                    "date_to": end.strftime("%Y-%m-%d"),
                    "grid_degrees": GRID_DEGREES,
                }
            )
        write_module_weather_outputs(outputs, output_paths)


if __name__ == "__main__":
    sys.stderr = open(snakemake.log[0], "w", buffering=1)
    process_gridded_weather_data(
        path_to_era5=snakemake.input.era5,
        output_paths={
            "grid": snakemake.output.grid,
            "temperature": snakemake.output.temperature,
            "wind10m": snakemake.output.wind10m,
            "tsoil5": snakemake.output.tsoil5,
        },
        weather_years=[int(year) for year in snakemake.params.weather_years],
    )
