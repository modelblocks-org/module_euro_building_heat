"""Download one reusable ERA5 file from Earth Data Hub."""

from datetime import datetime
from pathlib import Path

import geopandas as gpd
import numpy as np
import xarray as xr

EDH_URL = (
    "https://data.earthdatahub.destine.eu/era5/era5-single-levels-atmosphere-v0.zarr"
)
EDH_VARIABLES = ["t2m", "u10", "v10", "stl1"]
GRID_DEGREES = 0.25
WGS84 = "EPSG:4326"


def weather_time_range(weather_years: list[int]) -> tuple[datetime, datetime]:
    """Return the timestamp range needed by the heat profile calculation."""
    return (
        datetime(min(weather_years) - 1, 12, 25),
        datetime(max(weather_years) + 1, 1, 5, 23),
    )


def weather_bounds(shapes: gpd.GeoDataFrame) -> tuple[float, ...]:
    """Return native-grid-aligned WGS84 bounds with one grid-cell buffer."""
    west, south, east, north = shapes.to_crs(WGS84).total_bounds
    return (
        np.floor(west / GRID_DEGREES) * GRID_DEGREES - GRID_DEGREES,
        np.floor(south / GRID_DEGREES) * GRID_DEGREES - GRID_DEGREES,
        np.ceil(east / GRID_DEGREES) * GRID_DEGREES + GRID_DEGREES,
        np.ceil(north / GRID_DEGREES) * GRID_DEGREES + GRID_DEGREES,
    )


def select_edh_weather(
    dataset: xr.Dataset, bounds: tuple[float, ...], start: datetime, end: datetime
) -> xr.Dataset:
    """Select the requested EDH variables, area, and time range."""
    west, south, east, north = bounds
    dataset = dataset[EDH_VARIABLES].sel(
        valid_time=slice(start, end), latitude=slice(north, south)
    )
    dataset = dataset.assign_coords(longitude=((dataset.longitude + 180) % 360) - 180)
    return (
        dataset.where(
            (dataset.longitude >= west) & (dataset.longitude <= east), drop=True
        )
        .sortby("longitude")
        .rename({"valid_time": "time", "latitude": "lat", "longitude": "lon"})
        .sortby("lat")
    )


def download_era5_data(
    path_to_shapes: str | Path, output_path: str | Path, weather_years: list[int]
) -> None:
    """Download the complete EDH subset to one NetCDF file."""
    start, end = weather_time_range(weather_years)
    bounds = weather_bounds(gpd.read_parquet(path_to_shapes))
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with xr.open_dataset(
        EDH_URL,
        chunks={},
        engine="zarr",
        storage_options={"client_kwargs": {"trust_env": True}},
    ) as era5:
        dataset = select_edh_weather(era5, bounds, start, end)
        dataset.attrs.update(
            {
                "dataset": "era5-edh",
                "date_from": start.strftime("%Y-%m-%d"),
                "date_to": end.strftime("%Y-%m-%d"),
                "grid_degrees": GRID_DEGREES,
            }
        )
        dataset.to_netcdf(
            output_path,
            encoding={
                variable: {"zlib": True, "complevel": 1} for variable in EDH_VARIABLES
            },
        )


if __name__ == "__main__":
    download_era5_data(
        path_to_shapes=snakemake.input.shapes,
        output_path=snakemake.output.era5,
        weather_years=[int(year) for year in snakemake.params.weather_years],
    )
