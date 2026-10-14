"""Microsoft Global ML Building Footprints tiling and proxy calculations."""

import math
from collections import Counter

import pandas as pd
import pyarrow as pa
from shapely.geometry import box

MICROSOFT_COLUMNS = ["id", "quadkey", "region_id", "footprint_area_m2", "x", "y"]
MICROSOFT_SCHEMA = pa.schema(
    [
        ("id", pa.string()),
        ("quadkey", pa.string()),
        ("region_id", pa.string()),
        ("footprint_area_m2", pa.float64()),
        ("x", pa.float64()),
        ("y", pa.float64()),
    ]
)
MICROSOFT_TILE_STATISTICS_COLUMNS = ["quadkey", "building_count"]
MICROSOFT_TILE_STATISTICS_SCHEMA = pa.schema(
    [("quadkey", pa.string()), ("building_count", pa.int64())]
)

MICROSOFT_TOTALS_SCHEMA = pa.schema(
    [("region_id", pa.string()), ("footprint_area_m2", pa.float64())]
)


def tile_xy(lon: float, lat: float, zoom: int = 9) -> tuple[int, int]:
    """Return the Bing tile containing a WGS84 coordinate."""
    scale = 1 << zoom
    x = int((lon + 180) / 360 * scale)
    latitude = math.radians(max(-85.05112878, min(85.05112878, lat)))
    y = int((1 - math.asinh(math.tan(latitude)) / math.pi) / 2 * scale)
    return min(scale - 1, max(0, x)), min(scale - 1, max(0, y))


def quadkey(x: int, y: int, zoom: int = 9) -> str:
    """Encode Bing tile coordinates as a quadkey."""
    return "".join(
        str(((x & (1 << bit)) > 0) + 2 * ((y & (1 << bit)) > 0))
        for bit in range(zoom - 1, -1, -1)
    )


def quadkey_xy(key: str) -> tuple[int, int]:
    """Decode a Bing quadkey into its tile coordinates."""
    assert key
    assert set(key) <= set("0123")
    x = sum((int(digit) & 1) << bit for bit, digit in enumerate(reversed(key)))
    y = sum((int(digit) >> 1) << bit for bit, digit in enumerate(reversed(key)))
    return x, y


def tile_bounds(x: int, y: int, zoom: int = 9):
    """Return a WGS84 polygon for one Bing tile."""
    scale = 1 << zoom
    west, east = x / scale * 360 - 180, (x + 1) / scale * 360 - 180
    north = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / scale))))
    south = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / scale))))
    return box(west, south, east, north)


def quadkey_polygon(key: str):
    """Return the WGS84 polygon represented by a Bing quadkey."""
    return tile_bounds(*quadkey_xy(key), zoom=len(key))


def intersecting_quadkeys(geometry, zoom: int = 9) -> list[str]:
    """Return only level-nine Bing tiles intersecting a WGS84 geometry."""
    west, south, east, north = geometry.bounds
    left, top = tile_xy(west, north, zoom)
    right, bottom = tile_xy(east, south, zoom)
    return sorted(
        quadkey(x, y, zoom)
        for x in range(left, right + 1)
        for y in range(top, bottom + 1)
        if tile_bounds(x, y, zoom).intersects(geometry)
    )


def tile_statistics(planned_quadkeys, source_counts) -> pd.DataFrame:
    """Sum raw source-file feature counts and retain zero-count planned tiles."""
    counts = Counter(dict.fromkeys(planned_quadkeys, 0))
    for key, count in source_counts:
        assert key in counts
        assert count >= 0
        counts[key] += count
    return pd.DataFrame(
        {
            "quadkey": sorted(counts),
            "building_count": pd.Series(
                [counts[key] for key in sorted(counts)], dtype="int64"
            ),
        }
    )


def low_coverage_quadkeys(statistics: pd.DataFrame, minimum_count: int) -> set[str]:
    """Return quadkeys whose raw feature count is strictly below the threshold."""
    assert minimum_count >= 0
    return set(statistics.loc[statistics.building_count.lt(minimum_count), "quadkey"])
