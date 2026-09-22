"""General utility functions."""

import math
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
import pycountry
import rasterio
import rioxarray  # noqa: F401 -- register the xarray rio accessor
import shapely
import xarray as xr
from _schemas import (
    validate_nonnegative,
    validate_raster_contract,
    validate_residential_space_heat_weight_raster,
    validate_support_profiles,
)
from affine import Affine
from gregor.aggregate import aggregate_raster_to_polygon
from rasterio.enums import Resampling
from rasterio.features import geometry_mask
from rasterio.warp import reproject
from rasterio.windows import Window
from shapely.geometry import box
from shapely.geometry.base import BaseGeometry

EUROSTAT_TO_ALPHA3 = {"EL": "GRC", "UK": "GBR"}


def eurostat_to_alpha3(code: str) -> str:
    """Convert a Eurostat alpha-2-like country code to ISO alpha-3."""
    if code in EUROSTAT_TO_ALPHA3:
        return EUROSTAT_TO_ALPHA3[code]
    return pycountry.countries.get(alpha_2=code).alpha_3


SPACE_HEAT_WEIGHT_BANDS = ("residential_space_heat_weight",)


SUPPORT_BANDS = {
    "floor_area": ("residential", "commercial"),
    "residential_full": (
        "residential",
        "population",
        "sv_valid_floor_area",
        "sv_weighted_floor_area",
    ),
    "residential_scoped": (
        "population",
        "sv_valid_floor_area",
        "sv_weighted_floor_area",
    ),
}


SUPPORT_UNITS = {
    "floor_area": ("m2/ha", "m2/ha"),
    "residential_full": ("m2/ha", "people/ha", "m2/ha", "m2/ha * sv_power"),
    "residential_scoped": ("people/ha", "m2/ha", "m2/ha * sv_power"),
}


def write_raster(path, profile, values, bands, units, tags):
    """Persist aligned support arrays with their explicit band contract."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w+", **{**profile, "count": len(bands)}) as output:
        for index, (value, band, unit) in enumerate(
            zip(values, bands, units, strict=True), 1
        ):
            value = np.asarray(value, dtype=profile["dtype"])
            validate_nonnegative(value)
            output.write(value, index)
            output.set_band_description(index, band)
            output.set_band_unit(index, unit)
        output.update_tags(**tags)
        validate_raster_contract(output, bands, units, check_values=False)


def scope_geometry(shapes: gpd.GeoDataFrame, crs: Any) -> BaseGeometry:
    """Return the union of land shapes in the requested CRS."""
    return shapes.to_crs(crs).geometry.union_all()


def read_region_support(directory):
    """Load the three cached arrays; use the scoped floor grid for output.

    Complete-region support supplies normalization totals. The two scoped
    arrays retain the original centroid/cell clipping for the requested shapes.
    """
    arrays = []
    profiles = {}
    for kind in SUPPORT_BANDS:
        with rasterio.open(directory / f"{kind}.tif") as raster:
            validate_raster_contract(
                raster, SUPPORT_BANDS[kind], SUPPORT_UNITS[kind], check_values=False
            )
            profiles[kind] = raster.profile
            values = raster.read()
            validate_nonnegative(values)
            arrays.append(values)
            if kind == "floor_area":
                profile = raster.profile
    validate_support_profiles(profiles)
    return *arrays, profile


def processing_crs(shapes: gpd.GeoDataFrame) -> str:
    """Choose European LAEA only when the complete WGS84 scope lies in Europe."""
    europe = box(-35, 24, 45, 72)
    return (
        "EPSG:3035"
        if europe.covers(shapes.to_crs(4326).geometry.union_all())
        else "ESRI:54009"
    )


def points_within_scope(points, scope):
    """Test centroid coordinates against a prepared scope with strict containment."""
    return shapely.contains_xy(scope, points.x, points.y)


def output_profile(bounds, settings, crs="EPSG:3035", count=3):
    """Return an equal-area raster profile aligned to the hectare grid.

    Bounds are rounded outward to exact multiples of 100 metres. Every
    partial raster therefore has cell boundaries aligned with the final raster
    and can be merged by direct addition.
    """
    cell = 100
    left = math.floor(bounds[0] / cell) * cell
    bottom = math.floor(bounds[1] / cell) * cell
    right = math.ceil(bounds[2] / cell) * cell
    top = math.ceil(bounds[3] / cell) * cell
    return {
        "driver": "GTiff",
        "width": round((right - left) / cell),
        "height": round((top - bottom) / cell),
        "count": count,
        "dtype": settings["dtype"],
        "crs": crs,
        "transform": Affine(cell, 0, left, 0, -cell, top),
        "nodata": 0.0,
        "compress": settings["compression"],
        "tiled": True,
        "blockxsize": settings["block_size"],
        "blockysize": settings["block_size"],
    }


def population_grid(source, profile, geometry, total):
    """Reproject counts, retain region-centred cells, and conserve its total."""
    population = np.zeros((profile["height"], profile["width"]), dtype=float)
    reproject(
        rasterio.band(source, 1),
        population,
        dst_transform=profile["transform"],
        dst_crs=profile["crs"],
        dst_nodata=0,
        resampling=Resampling.sum,
    )
    population[population == source.nodata] = 0
    population[geometry_mask([geometry], population.shape, profile["transform"])] = 0
    assert population.sum() > 0 or total == 0
    if total > 0:
        population *= total / population.sum()
    return population


def point_grid(profile, points, values):
    """Sum point values into an aligned raster array."""
    grid = np.zeros((profile["height"], profile["width"]), dtype=float)
    if points.empty:
        return grid
    rows, columns = rasterio.transform.rowcol(
        profile["transform"], points.geometry.x, points.geometry.y
    )
    np.add.at(grid, (np.asarray(rows), np.asarray(columns)), np.asarray(values))
    return grid


def clipped_grid(grid, source_profile, profile, geometry):
    """Extract one aligned shape window and retain cells centred in its geometry."""
    bounds = rasterio.transform.array_bounds(
        profile["height"], profile["width"], profile["transform"]
    )
    raw = rasterio.windows.from_bounds(*bounds, transform=source_profile["transform"])
    window = Window(
        round(raw.col_off), round(raw.row_off), round(raw.width), round(raw.height)
    )
    clipped = grid[window.toslices()].copy()
    clipped[geometry_mask([geometry], clipped.shape, profile["transform"])] = 0
    return clipped


def add_partial(output, partial, bands=(1, 2), source_bands=None) -> None:
    """Add aligned bands in bounded windows, optionally mapping source bands."""
    raw = rasterio.windows.from_bounds(*partial.bounds, output.transform)
    for _, window in partial.block_windows(1):
        destination = Window(
            round(raw.col_off) + window.col_off,
            round(raw.row_off) + window.row_off,
            window.width,
            window.height,
        )
        output.write(
            output.read(bands, window=destination)
            + partial.read(source_bands or bands, window=window),
            bands,
            window=destination,
        )


def intersecting_windows(raster, polygons, block_size=512):
    """Visit bounded blocks inside the polygon extent, including untiled GHSL files."""
    extent = rasterio.windows.from_bounds(
        *polygons.total_bounds, transform=raster.transform
    )
    left = max(0, int(extent.col_off // block_size) * block_size)
    top = max(0, int(extent.row_off // block_size) * block_size)
    right = min(
        raster.width,
        math.ceil((extent.col_off + extent.width) / block_size) * block_size,
    )
    bottom = min(
        raster.height,
        math.ceil((extent.row_off + extent.height) / block_size) * block_size,
    )
    for row in range(top, bottom, block_size):
        for column in range(left, right, block_size):
            yield Window(
                column,
                row,
                min(block_size, raster.width - column),
                min(block_size, raster.height - row),
            )


def window_polygons(polygons, window, transform):
    """Clip candidate polygons to a window, preserving positional identifiers."""
    bounds = shapely.box(*rasterio.windows.bounds(window, transform))
    candidates = polygons.sindex.query(bounds, predicate="intersects")
    local = polygons.iloc[candidates][["geometry"]].copy()
    local.geometry = local.geometry.intersection(bounds)
    return local.loc[local.area > 0]


def _iter_raster_blocks(raster_path, polygons, sector="residential", block_size=512):
    """Yield bounded raster windows and clipped polygons on the native source grid."""
    if sector is not None:
        validate_residential_space_heat_weight_raster(
            raster_path, check_values=False, sector=sector
        )
    with rasterio.open(raster_path) as raster:
        projected = polygons.to_crs(raster.crs).reset_index(drop=True)
        if projected.empty:
            return
        transform = raster.transform
        if transform.b != 0 or transform.d != 0 or transform.a <= 0 or transform.e >= 0:
            raise ValueError("Spatial support must use a north-up raster grid.")
        for window in intersecting_windows(raster, projected, block_size):
            local = window_polygons(projected, window, transform)
            if local.empty:
                continue
            values = raster.read(1, window=window, masked=True).filled(0)
            if not np.isfinite(values).all() or (values < 0).any():
                raise ValueError(f"Invalid spatial support in raster window {window}.")
            yield window, values, local, raster.window_transform(window)


def aggregate_support(
    raster_path: str,
    polygons: gpd.GeoDataFrame,
    sector: str | None = "residential",
    block_size: int = 512,
) -> np.ndarray:
    """Accumulate Gregor zonal sums while loading only one raster block at a time."""
    weights = np.zeros(len(polygons), dtype=np.float64)
    for _, values, local, transform in _iter_raster_blocks(
        raster_path, polygons, sector, block_size
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


def population_summaries(path, groups, block_size=2048):
    """Aggregate one population source once per exact projected geometry.

    Retain separate regional, floor-reference and count-reference rows: their
    coverage need not match even when they describe the same administrative ID.
    """
    with rasterio.open(path) as raster:
        crs = raster.crs
    polygons = gpd.GeoDataFrame(
        pd.concat(
            [
                frame[["region_id", "country_id", "geometry"]]
                .to_crs(crs)
                .assign(population_kind=kind)
                for kind, frame in groups.items()
            ],
            ignore_index=True,
        ),
        crs=crs,
    )
    codes, geometries = pd.factorize(polygons.geometry.to_wkb())
    unique = gpd.GeoDataFrame(geometry=gpd.GeoSeries.from_wkb(geometries, crs=crs))
    values = aggregate_support(path, unique, sector=None, block_size=block_size)
    return pd.DataFrame(polygons.drop(columns="geometry")).assign(
        population=values[codes]
    )
