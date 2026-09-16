"""Windowed Gregor aggregation of structural residential heat support.

Gregor assigns cells by pixel centre. Clip polygons to each raster window so
neither Gregor nor rasterstats needs to allocate a full country-sized raster.
The demand raster uses the same rasterstats/rasterio pixel assignment.
"""

import logging
from collections.abc import Iterator

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import rioxarray  # noqa: F401 -- register the xarray rio accessor
import shapely
import xarray as xr
from _schemas import (
    ResidentialSpaceHeatWeightSchema,
    ShapesSchema,
    validate_residential_space_heat_weight_raster,
)
from gregor.aggregate import aggregate_raster_to_polygon
from rasterio.features import geometry_mask

logger = logging.getLogger(__name__)


def _iter_raster_blocks(
    raster_path: str, polygons: gpd.GeoDataFrame, sector: str = "residential"
) -> Iterator[tuple]:
    """Yield validated raster blocks with intersecting, clipped polygons."""
    validate_residential_space_heat_weight_raster(
        raster_path, check_values=False, sector=sector
    )
    with rasterio.open(raster_path) as raster:
        projected = polygons.to_crs(raster.crs).reset_index(drop=True)
        spatial_index = projected.sindex
        transform = raster.transform
        if transform.b != 0 or transform.d != 0 or transform.a <= 0 or transform.e >= 0:
            raise ValueError("Structural support must use a north-up raster grid.")
        block_height, block_width = raster.block_shapes[0]
        total_blocks = (
            (raster.height + block_height - 1)
            // block_height
            * ((raster.width + block_width - 1) // block_width)
        )
        for number, (_, window) in enumerate(raster.block_windows(1), start=1):
            if number == 1 or number % 100 == 0 or number == total_blocks:
                logger.info("Structural raster block %s/%s", number, total_blocks)
            values = raster.read(1, window=window)
            if not np.isfinite(values).all() or (values < 0).any():
                raise ValueError(
                    f"Invalid structural support in raster window {window}."
                )
            bounds = shapely.box(*rasterio.windows.bounds(window, transform))
            if (values > 0).any():
                candidates = spatial_index.query(bounds, predicate="intersects")
                local = projected.iloc[candidates][["geometry"]].copy()
                local.geometry = local.geometry.intersection(bounds)
                local = local.loc[local.area > 0]
            else:
                local = projected.iloc[:0][["geometry"]]
            yield window, values, local, raster.window_transform(window)


def iter_weight_blocks(
    raster_path: str, polygons: gpd.GeoDataFrame, sector: str = "residential"
) -> Iterator[tuple]:
    """Yield sparse support contributions using Gregor's pixel-centre convention."""
    for window, values, local, transform in _iter_raster_blocks(
        raster_path, polygons, sector
    ):
        contributions = []
        for index, geometry in local.geometry.items():
            mask = geometry_mask(
                [geometry],
                out_shape=values.shape,
                transform=transform,
                invert=True,
                all_touched=False,
            ) & (values > 0)
            rows, columns = np.nonzero(mask)
            contributions.append(
                (index, rows, columns, values[rows, columns].astype(np.float64))
            )
        yield window, values.shape, contributions


def aggregate_support(
    raster_path: str, polygons: gpd.GeoDataFrame, sector: str = "residential"
) -> np.ndarray:
    """Accumulate Gregor zonal sums while loading only one raster block at a time."""
    weights = np.zeros(len(polygons), dtype=np.float64)
    for _, values, local, transform in _iter_raster_blocks(
        raster_path, polygons, sector
    ):
        if local.empty:
            continue
        raster = (
            xr.DataArray(
                values,
                dims=("y", "x"),
                coords={
                    "x": transform.c + (np.arange(values.shape[1]) + 0.5) * transform.a,
                    "y": transform.f + (np.arange(values.shape[0]) + 0.5) * transform.e,
                },
            )
            .rio.write_crs(local.crs)
            .rio.write_transform(transform)
        )
        aggregated = aggregate_raster_to_polygon(raster, local, stats="sum")
        weights[local.index.to_numpy()] += aggregated["sum"].fillna(0).to_numpy()
    return weights


def aggregate_weight(raster_path: str, shapes: gpd.GeoDataFrame) -> pd.DataFrame:
    """Return Gregor support sums for each Modelblocks shape."""
    shapes = ShapesSchema.validate(shapes).reset_index(drop=True)
    return ResidentialSpaceHeatWeightSchema.validate(
        pd.DataFrame(
            {
                "shape_id": shapes.shape_id,
                "country_id": shapes.country_id,
                "weight": aggregate_support(raster_path, shapes),
            }
        )
    )
