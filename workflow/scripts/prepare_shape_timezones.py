"""Assign an IANA timezone to each prepared shape using its centroid."""

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import geopandas as gpd
import pandas as pd
import shapely
from pyproj import CRS

if TYPE_CHECKING:
    snakemake: Any


WGS84 = "EPSG:4326"


def _normalise_shape_ids(values: pd.Series) -> pd.Series:
    """Match the xarray-safe shape IDs used elsewhere in the workflow."""
    return values.astype(str).str.replace(".", "-", regex=False)


def assign_shape_timezones(
    shapes: gpd.GeoDataFrame,
    timezone_boundaries: gpd.GeoDataFrame,
    projected_crs: str | int,
) -> pd.DataFrame:
    """Assign the unique timezone polygon intersecting each shape centroid."""
    centroid_crs = CRS.from_user_input(projected_crs)
    if not centroid_crs.is_projected:
        raise ValueError(f"The provided crs must be projected: {centroid_crs}.")

    shapes_projected = (
        shapes.loc[:, ["shape_id", "geometry"]].to_crs(centroid_crs).copy()
    )
    shapes_projected["shape_id"] = _normalise_shape_ids(shapes_projected["shape_id"])

    timezone_boundaries = timezone_boundaries.loc[:, ["tzid", "geometry"]].to_crs(WGS84)
    centroids = gpd.GeoDataFrame(
        {"shape_id": shapes_projected["shape_id"].to_numpy()},
        geometry=shapely.centroid(shapes_projected.geometry.array),
        crs=centroid_crs,
    ).to_crs(WGS84)
    centroids["centroid_lon"] = shapely.get_x(centroids.geometry.array)
    centroids["centroid_lat"] = shapely.get_y(centroids.geometry.array)

    matches = gpd.sjoin(
        centroids, timezone_boundaries, how="left", predicate="intersects"
    )
    timezone_matches = {
        shape_id: sorted(set(group["tzid"].dropna().astype(str)))
        for shape_id, group in matches.groupby("shape_id", sort=False)
    }

    failures = []
    selected = []
    for row in centroids.itertuples(index=False):
        tzids = timezone_matches.get(row.shape_id, [])
        coordinate = f"({row.centroid_lon:.6f}, {row.centroid_lat:.6f})"
        if len(tzids) == 0:
            failures.append(
                f"shape_id={row.shape_id!r}, centroid={coordinate}: no timezone match"
            )
            continue
        if len(tzids) > 1:
            failures.append(
                f"shape_id={row.shape_id!r}, centroid={coordinate}: "
                f"multiple timezone matches {tzids}"
            )
            continue
        try:
            ZoneInfo(tzids[0])
        except ZoneInfoNotFoundError:
            failures.append(
                f"shape_id={row.shape_id!r}, centroid={coordinate}: "
                f"unknown IANA timezone {tzids[0]!r}"
            )
            continue
        selected.append(
            {
                "shape_id": row.shape_id,
                "timezone": tzids[0],
                "centroid_lon": row.centroid_lon,
                "centroid_lat": row.centroid_lat,
            }
        )

    if failures:
        raise ValueError(
            "Each shape centroid must match exactly one valid IANA timezone:\n- "
            + "\n- ".join(failures)
        )
    return pd.DataFrame(selected)


def prepare_shape_timezones(
    shapes_path: str | Path,
    timezone_boundaries_path: str | Path,
    projected_crs: str | int,
    output_path: str | Path,
) -> None:
    """Read geometry inputs, assign timezones, and write the internal mapping."""
    shapes = gpd.read_parquet(shapes_path)
    timezone_boundaries = gpd.read_file(timezone_boundaries_path)
    mapping = assign_shape_timezones(shapes, timezone_boundaries, projected_crs)
    mapping.to_parquet(output_path, index=False)


if __name__ == "__main__":
    sys.stderr = open(snakemake.log[0], "w", buffering=1)
    prepare_shape_timezones(
        shapes_path=snakemake.input.shapes,
        timezone_boundaries_path=snakemake.input.timezone_boundaries,
        projected_crs=snakemake.params.projected_crs,
        output_path=snakemake.output[0],
    )
