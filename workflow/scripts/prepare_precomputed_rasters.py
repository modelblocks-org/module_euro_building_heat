"""Window published grids to user shapes without resampling hectare values."""

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import _plots
import geopandas as gpd
import numpy as np
import rasterio
from _utils import output_profile, processing_crs
from rasterio.features import geometry_mask
from rasterio.windows import Window

if TYPE_CHECKING:
    snakemake: Any


def crop_grid(source, target, shapes, settings):
    """Copy aligned blocks, rejecting unsupported coverage or grid alignment."""
    crs = processing_crs(shapes)
    shapes = shapes.to_crs(crs)
    profile = output_profile(shapes.total_bounds, settings, crs, count=1)
    geometry = shapes.geometry.union_all()
    with rasterio.open(source) as src:
        transform = profile["transform"]
        col, row = ~src.transform * (transform.c, transform.f)
        if (
            src.crs != rasterio.crs.CRS.from_user_input(crs)
            or src.count != 1
            or src.transform.a != transform.a
            or src.transform.e != transform.e
            or src.transform.b != 0
            or src.transform.d != 0
            or not np.allclose([col, row], np.round([col, row]), rtol=0, atol=1e-6)
        ):
            raise ValueError(
                "Published grid is incompatible; use building_rasters.source: rebuild."
            )
        col, row = round(col), round(row)
        if (
            col < 0
            or row < 0
            or col + profile["width"] > src.width
            or row + profile["height"] > src.height
        ):
            raise ValueError(
                "Requested shapes exceed published grid coverage; use building_rasters.source: rebuild."
            )
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(target, "w", **profile) as dst:
            dst.update_tags(**src.tags())
            dst.update_tags(building_raster_source="zenodo")
            if src.descriptions[0]:
                dst.set_band_description(1, src.descriptions[0])
            if src.units[0]:
                dst.set_band_unit(1, src.units[0])
            for _, window in dst.block_windows(1):
                values = src.read(
                    1,
                    window=Window(
                        col + window.col_off,
                        row + window.row_off,
                        window.width,
                        window.height,
                    ),
                    masked=True,
                )
                inside = geometry_mask(
                    [geometry], values.shape, dst.window_transform(window), invert=True
                )
                if np.any(np.ma.getmaskarray(values) & inside) and src.nodata != 0:
                    raise ValueError(
                        "Published grid has missing coverage inside the requested shapes."
                    )
                values = values.filled(0)
                if not np.isfinite(values).all() or (values < 0).any():
                    raise ValueError(
                        "Published grids must contain finite, nonnegative values."
                    )
                values[~inside] = 0
                dst.write(values, 1, window=window)
    return shapes


def main():
    """Prepare the same raster and report interfaces as the building branch."""
    shapes = gpd.read_parquet(snakemake.input.shapes)
    labels = {
        "building_count": (
            "Residential, commercial and public buildings",
            "Buildings per hectare",
        ),
        "residential_space_heat_weight": (
            "Residential space-heating support",
            "Space-heating support (weighted m²/ha)",
        ),
        "commercial_space_heat_weight": (
            "Commercial and public floor area",
            "Floor area (m²/ha)",
        ),
    }
    for dataset in snakemake.params.datasets:
        projected = crop_grid(
            snakemake.input[dataset],
            snakemake.output[dataset],
            shapes,
            snakemake.params.raster,
        )
        title, unit = labels[dataset]
        Path(snakemake.output[f"{dataset}_plot"]).parent.mkdir(
            parents=True, exist_ok=True
        )
        _plots.plot_floor_area(
            snakemake.output[dataset],
            1,
            title,
            snakemake.output[f"{dataset}_plot"],
            snakemake.params.population["chunk_size"],
            snakemake.params.plotting["max_size"],
            projected,
            snakemake.params.plotting["outline"],
            unit,
        )


if __name__ == "__main__":
    sys.stderr = open(snakemake.log[0], "w")
    main()
