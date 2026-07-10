"""Download monthly ERA5 weather files with cdsapi.

Downloads GRIB from CDS for efficient retrieval, then converts it locally to NetCDF.
"""

import calendar
import logging
import math
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

import cdsapi
import cfgrib
import geopandas as gpd
import xarray as xr

if TYPE_CHECKING:
    snakemake: Any

ERA5_CRS = "EPSG:4326"
ERA5_START = 1940
ERA5_DATASET = "reanalysis-era5-single-levels"

ERA5_VARIABLES = [
    "2m_temperature",
    "10m_u_component_of_wind",
    "10m_v_component_of_wind",
    "soil_temperature_level_1",
]

HOURS = [f"{hour:02d}:00" for hour in range(24)]
GRID_DEG = 0.5

logger = logging.getLogger(__name__)


def area_from_shapes(shapes: gpd.GeoDataFrame) -> tuple[float, ...]:
    """Return padded, grid-aligned CDS area [north, west, south, east]."""
    west, south, east, north = shapes.to_crs(ERA5_CRS).total_bounds

    west = math.floor((west - GRID_DEG) / GRID_DEG) * GRID_DEG
    south = math.floor((south - GRID_DEG) / GRID_DEG) * GRID_DEG
    east = math.ceil((east + GRID_DEG) / GRID_DEG) * GRID_DEG
    north = math.ceil((north + GRID_DEG) / GRID_DEG) * GRID_DEG

    return (
        min(90.0, round(north, 8)),
        max(-180.0, round(west, 8)),
        max(-90.0, round(south, 8)),
        min(180.0, round(east, 8)),
    )


def year_month_from_output(path: str | Path) -> tuple[int, int]:
    """Parse year/month from an output filename like heat_2020_01.nc."""
    match = re.search(r"heat_(\d{4})_(\d{2})\.nc$", Path(path).name)
    if not match:
        raise ValueError(f"Expected output filename like heat_2020_01.nc, got: {path}")

    year = int(match.group(1))
    month = int(match.group(2))

    if year < ERA5_START:
        raise ValueError(f"Invalid year in output filename: {path}")
    if not 1 <= month <= 12:
        raise ValueError(f"Invalid month in output filename: {path}")

    return year, month


def cds_request(area: Sequence[float], year: int, month: int) -> dict[str, Any]:
    """Build one full-month ERA5 CDS request."""
    days = calendar.monthrange(year, month)[1]

    return {
        "product_type": ["reanalysis"],
        "variable": ERA5_VARIABLES,
        "year": [f"{year:04d}"],
        "month": [f"{month:02d}"],
        "day": [f"{day:02d}" for day in range(1, days + 1)],
        "time": HOURS,
        "area": list(area),
        "grid": f"{GRID_DEG}/{GRID_DEG}",
        "data_format": "grib",
        "download_format": "unarchived",
    }


def grib_to_netcdf(grib_path: Path, output_path: Path) -> None:
    """Convert all compatible datasets in an ERA5 GRIB file to one NetCDF file."""
    datasets = cfgrib.open_datasets(
        grib_path, backend_kwargs={"indexpath": ""}, decode_timedelta=True
    )
    xr.merge(datasets, compat="override").to_netcdf(output_path)


def download_monthly_file(output_path: str | Path, area: Sequence[float]) -> Path:
    """Download one monthly ERA5 file as GRIB, then save it as NetCDF."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    year, month = year_month_from_output(output_path)
    request = cds_request(area, year, month)

    logger.info("Downloading ERA5 %04d-%02d to %s", year, month, output_path)
    logger.info(
        "CDS request: dataset=%s variables=%s area=%s grid=%s format=grib",
        ERA5_DATASET,
        ",".join(ERA5_VARIABLES),
        area,
        f"{GRID_DEG}/{GRID_DEG}",
    )

    grib_path = output_path.with_suffix(".grib")
    if grib_path.exists():
        logger.info("Reusing previously downloaded GRIB file %s", grib_path)
    else:
        partial_grib = grib_path.with_suffix(".grib.part")
        client = cdsapi.Client(quiet=True, progress=False)
        try:
            client.retrieve(ERA5_DATASET, request, str(partial_grib))
            partial_grib.replace(grib_path)
        finally:
            partial_grib.unlink(missing_ok=True)

    logger.info("Converting downloaded GRIB to NetCDF")
    grib_to_netcdf(grib_path, output_path)
    grib_path.unlink()

    logger.info("Saved ERA5 %04d-%02d to %s", year, month, output_path)
    return output_path


def main() -> None:
    """Main Snakemake process."""
    shapes = gpd.read_parquet(snakemake.input.shapes)
    area = area_from_shapes(shapes)
    download_monthly_file(snakemake.output.era5_heat, area)


if __name__ == "__main__":
    sys.stderr = open(snakemake.log[0], "w", buffering=1)
    sys.stdout = sys.stderr
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", force=True
    )
    main()
