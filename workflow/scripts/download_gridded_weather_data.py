"""Download raw ERA5 weather GRIB files from CDS."""

import calendar
import logging
import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

ERA5_DATASET = "reanalysis-era5-single-levels"
ERA5_VARIABLES = [
    "2m_temperature",
    "10m_u_component_of_wind",
    "10m_v_component_of_wind",
    "soil_temperature_level_1",
]
HOURS = [f"{hour:02d}:00" for hour in range(24)]
GRID_DEGREES = 0.5
BUFFER_DEGREES = 0.5
DATA_FORMAT = "grib"

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WeatherBounds:
    """Weather cutout bounds in WGS84 degrees."""

    west: float
    south: float
    east: float
    north: float

    def cds_area(self) -> list[float]:
        """Return CDS API area order: north, west, south, east."""
        return [self.north, self.west, self.south, self.east]


def aligned_bounds(
    bounds: tuple[float, float, float, float],
    grid_degrees: float = GRID_DEGREES,
    buffer_degrees: float = BUFFER_DEGREES,
) -> WeatherBounds:
    """Pad and align WGS84 bounds to the ERA5 latitude/longitude grid."""
    west, south, east, north = bounds
    west = math.floor((west - buffer_degrees) / grid_degrees) * grid_degrees
    south = math.floor((south - buffer_degrees) / grid_degrees) * grid_degrees
    east = math.ceil((east + buffer_degrees) / grid_degrees) * grid_degrees
    north = math.ceil((north + buffer_degrees) / grid_degrees) * grid_degrees

    return WeatherBounds(
        west=max(-180.0, round(west, 8)),
        south=max(-90.0, round(south, 8)),
        east=min(180.0, round(east, 8)),
        north=min(90.0, round(north, 8)),
    )


def weather_time_range(weather_years: list[int]) -> tuple[datetime, datetime]:
    """Return the timestamp range needed by the heat profile calculation."""
    start_year = min(weather_years)
    end_year = max(weather_years)
    return (datetime(start_year - 1, 12, 25, 0), datetime(end_year + 1, 1, 5, 23))


def time_request_chunks(start: datetime, end: datetime) -> list[dict[str, list[str]]]:
    """Build yearly hourly CDS requests for a closed timestamp range."""
    chunks = []
    for year in range(start.year, end.year + 1):
        first_month = start.month if year == start.year else 1
        last_month = end.month if year == end.year else 12

        if first_month == last_month:
            first_day = start.day if year == start.year else 1
            last_day = (
                end.day
                if year == end.year
                else calendar.monthrange(year, first_month)[1]
            )
            days = [f"{day:02d}" for day in range(first_day, last_day + 1)]
        else:
            days = [f"{day:02d}" for day in range(1, 32)]

        chunks.append(
            {
                "year": [f"{year:04d}"],
                "month": [
                    f"{month:02d}" for month in range(first_month, last_month + 1)
                ],
                "day": days,
                "time": HOURS,
            }
        )
    return chunks


def shape_weather_bounds(path_to_shapes: str | Path) -> WeatherBounds:
    """Read shapes and return padded, grid-aligned WGS84 weather bounds."""
    import geopandas as gpd

    shapes = gpd.read_parquet(path_to_shapes)
    return aligned_bounds(tuple(shapes.to_crs("EPSG:4326").total_bounds))


def cds_request(
    area: WeatherBounds, time_chunk: dict[str, list[str]]
) -> dict[str, Any]:
    """Build a CDS ERA5 request for one time chunk."""
    return {
        "product_type": ["reanalysis"],
        "variable": ERA5_VARIABLES,
        "year": time_chunk["year"],
        "month": time_chunk["month"],
        "day": time_chunk["day"],
        "time": time_chunk["time"],
        "area": area.cds_area(),
        "grid": f"{GRID_DEGREES}/{GRID_DEGREES}",
        "data_format": DATA_FORMAT,
        "download_format": "unarchived",
    }


def download_era5_requests(
    output_dir: Path,
    area: WeatherBounds,
    time_chunks: list[dict[str, list[str]]],
    max_workers: int,
) -> list[Path]:
    """Download ERA5 request chunks and return local file paths."""
    import cdsapi

    output_dir.mkdir(parents=True, exist_ok=True)

    def download_one(index_and_chunk: tuple[int, dict[str, list[str]]]) -> Path:
        i, time_chunk = index_and_chunk
        client = cdsapi.Client(quiet=True, progress=False)
        request = cds_request(area, time_chunk)
        target = output_dir / f"era5_{time_chunk['year'][0]}.grib"
        logger.info("Requesting ERA5 chunk %s/%s: %s", i + 1, len(time_chunks), request)
        client.retrieve(ERA5_DATASET, request, str(target))
        return target

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        return list(executor.map(download_one, enumerate(time_chunks)))


def download_raw_weather_data(
    path_to_shapes: str | Path,
    output_dir: str | Path,
    weather_years: list[int],
    download_workers: int,
) -> None:
    """Download raw yearly ERA5 GRIB chunks."""
    area = shape_weather_bounds(path_to_shapes)
    start, end = weather_time_range(weather_years)
    time_chunks = time_request_chunks(start, end)

    logger.info("Downloading ERA5 area %s for %s to %s", area, start, end)
    download_era5_requests(Path(output_dir), area, time_chunks, download_workers)


if __name__ == "__main__":
    log_path = Path(snakemake.log[0])
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=log_path,
        level=logging.INFO,
        format="%(levelname)s:%(name)s:%(message)s",
        force=True,
    )
    download_raw_weather_data(
        path_to_shapes=snakemake.input.locations,
        output_dir=snakemake.output.raw_weather,
        weather_years=[int(year) for year in snakemake.params.weather_years],
        download_workers=int(snakemake.params.download_workers),
    )
