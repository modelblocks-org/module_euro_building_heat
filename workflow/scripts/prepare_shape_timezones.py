"""Assign an IANA timezone to each prepared shape using its centroid."""

import sys
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import geopandas as gpd
import pandas as pd
import shapely
from _timeseries import boundary_metadata, write_parquet_with_metadata

WGS84 = "EPSG:4326"


def _normalise_shape_ids(values: pd.Series) -> pd.Series:
    """Match the xarray-safe shape IDs used elsewhere in the workflow."""
    return values.astype(str).str.replace(".", "-", regex=False)


def assign_shape_timezones(
    shapes: gpd.GeoDataFrame, timezone_boundaries: gpd.GeoDataFrame
) -> pd.DataFrame:
    """Assign the unique timezone polygon intersecting each shape centroid."""
    shapes_wgs84 = shapes.loc[:, ["shape_id", "geometry"]].to_crs(WGS84).copy()
    shapes_wgs84["shape_id"] = _normalise_shape_ids(shapes_wgs84["shape_id"])

    timezone_boundaries = timezone_boundaries.loc[:, ["tzid", "geometry"]].to_crs(WGS84)
    centroid_geometry = shapely.centroid(shapes_wgs84.geometry.array)
    centroids = gpd.GeoDataFrame(
        {
            "shape_id": shapes_wgs84["shape_id"].to_numpy(),
            "centroid_lon": shapely.get_x(centroid_geometry),
            "centroid_lat": shapely.get_y(centroid_geometry),
        },
        geometry=centroid_geometry,
        crs=WGS84,
    )

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
    output_path: str | Path,
    metadata: dict[str, str],
) -> None:
    """Read geometry inputs, assign timezones, and write the internal mapping."""
    shapes = gpd.read_parquet(shapes_path)
    timezone_boundaries = gpd.read_file(timezone_boundaries_path)
    mapping = assign_shape_timezones(shapes, timezone_boundaries)
    write_parquet_with_metadata(mapping, output_path, metadata)


if __name__ == "__main__":
    sys.stderr = open(snakemake.log[0], "w", buffering=1)
    prepare_shape_timezones(
        shapes_path=snakemake.input.shapes,
        timezone_boundaries_path=snakemake.input.timezone_boundaries,
        output_path=snakemake.output[0],
        metadata=boundary_metadata(
            source=snakemake.params.source,
            release=snakemake.params.release,
            sha256=snakemake.params.sha256,
            attribution=snakemake.params.attribution,
        ),
    )
