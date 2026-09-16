"""Export annual heat demand by sink on the residential support raster grid."""

import sys
from collections.abc import Iterator
from contextlib import ExitStack
from pathlib import Path
from typing import TYPE_CHECKING, Any

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.vrt import WarpedVRT
from shapely.geometry import box

if TYPE_CHECKING:
    snakemake: Any


def shape_blocks(reference, shapes: gpd.GeoDataFrame) -> Iterator:
    """Assign output cells to shapes by pixel centre in bounded windows."""
    spatial_index = shapes.sindex
    for _, window in reference.block_windows(1):
        bounds = box(*rasterio.windows.bounds(window, reference.transform))
        indices = np.sort(spatial_index.query(bounds, predicate="intersects"))
        labels = np.zeros((int(window.height), int(window.width)), dtype="int32")
        if len(indices):
            rasterize(
                [(shapes.geometry.iloc[i], int(i + 1)) for i in indices],
                out=labels,
                transform=reference.window_transform(window),
                all_touched=False,
            )
        yield window, labels


def support_block(source, band: int, window) -> np.ndarray:
    """Read finite, non-negative support, treating nodata as zero."""
    values = source.read(band, window=window, masked=True).filled(0)
    if not np.isfinite(values).all() or (values < 0).any():
        raise ValueError("Heat demand support must be finite and non-negative.")
    return values


def write_heat_demand_rasters(
    annual_demand: pd.DataFrame,
    shapes: gpd.GeoDataFrame,
    household_path: str,
    residential_support_path: str,
    output_paths: dict[str, str],
    commercial_path: str,
    commercial_support_path: str,
) -> None:
    """Allocate each sector separately and preserve annual shape totals.

    Space heat retains each sector's structural/HDD pattern. Hot water uses
    the original sector structural proxies without HDD. Both sectors are
    normalised per shape and summed on the household space-heat raster grid.
    Each band inherits its demand/weather year pair from the household raster.
    """
    with ExitStack() as stack:
        household = stack.enter_context(rasterio.open(household_path))

        def aligned(path):
            source = stack.enter_context(rasterio.open(path))
            return stack.enter_context(
                WarpedVRT(
                    source,
                    crs=household.crs,
                    transform=household.transform,
                    width=household.width,
                    height=household.height,
                    resampling=Resampling.average,
                    dtype="float64",
                    nodata=0,
                )
            )

        commercial = aligned(commercial_path)
        if commercial.count != household.count or any(
            commercial.tags(band) != household.tags(band) for band in household.indexes
        ):
            raise ValueError("Household and commercial raster year pairs must match.")
        supports = {}
        if "space_heat" in output_paths:
            supports["space_heat", "household"] = household
            supports["space_heat", "commercial"] = commercial
        if "hot_water" in output_paths:
            supports["hot_water", "household"] = aligned(residential_support_path)
            supports["hot_water", "commercial"] = aligned(commercial_support_path)
        if set(output_paths) - {"space_heat", "hot_water"}:
            raise ValueError("Only space_heat and hot_water rasters are supported.")

        shapes = shapes.to_crs(household.crs).reset_index(drop=True)
        shape_ids = pd.Index(shapes.shape_id)
        if shape_ids.has_duplicates:
            raise ValueError("Shapes contain duplicate shape IDs.")
        count = len(shapes) + 1
        totals = {
            (sink, category, band): np.zeros(count)
            for sink, category in supports
            for band in (household.indexes if sink == "space_heat" else [1])
        }
        for window, labels in shape_blocks(household, shapes):
            for (sink, category, band), total in totals.items():
                total += np.bincount(
                    labels.ravel(),
                    weights=support_block(
                        supports[sink, category], band, window
                    ).ravel(),
                    minlength=count,
                )

        outputs = {}
        for sink, path in output_paths.items():
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            output = stack.enter_context(
                rasterio.open(path, "w", **household.profile, BIGTIFF="IF_SAFER")
            )
            output.update_tags(
                end_use=sink,
                categories="household,commercial",
                units="MWh/cell",
                allocation=(
                    "sector structural support * HDD^elasticity normalised per shape"
                    if sink == "space_heat"
                    else "sector structural support without HDD normalised per shape"
                ),
            )
            outputs[sink] = output

        factors = {}
        expected = {}
        for band in household.indexes:
            tags = household.tags(band)
            demand_year = int(tags["demand_year"])
            annual = annual_demand.loc[annual_demand.year == demand_year]
            for sink, output in outputs.items():
                selected = annual.loc[annual.end_use == sink]
                expected[band, sink] = (
                    selected.groupby("shape_id")
                    .heat_demand_twh.sum()
                    .reindex(shape_ids)
                    .to_numpy()
                    * 1e6
                )
                for category in ["household", "commercial"]:
                    energy = (
                        selected.loc[selected.category == category]
                        .groupby("shape_id")
                        .heat_demand_twh.sum()
                        .reindex(shape_ids)
                        .to_numpy()
                        * 1e6
                    )
                    if not np.isfinite(energy).all() or (energy < 0).any():
                        raise ValueError(
                            f"Invalid or missing {category} {sink} demand for {demand_year}."
                        )
                    support_band = band if sink == "space_heat" else 1
                    support = totals[sink, category, support_band][1:]
                    missing = (support <= 0) & (energy > 0)
                    if missing.any():
                        raise ValueError(
                            f"No {category} {sink} support on output grid for "
                            f"{shape_ids[missing].tolist()}."
                        )
                    scale = np.zeros(count)
                    np.divide(energy, support, out=scale[1:], where=support > 0)
                    factors[band, sink, category] = scale
                output.set_band_description(
                    band, f"{sink}_{demand_year}_weather_{tags['weather_year']}"
                )
                output.set_band_unit(band, "MWh/cell")
                output.update_tags(band, **tags)

        assigned = {key: np.zeros(count) for key in expected}
        for window, labels in shape_blocks(household, shapes):
            blocks = {
                key: support_block(supports[key[:2]], key[2], window) for key in totals
            }
            for band in household.indexes:
                for sink, output in outputs.items():
                    support_band = band if sink == "space_heat" else 1
                    energy = sum(
                        blocks[sink, category, support_band]
                        * factors[band, sink, category][labels]
                        for category in ["household", "commercial"]
                    )
                    assigned[band, sink] += np.bincount(
                        labels.ravel(), weights=energy.ravel(), minlength=count
                    )
                    output.write(energy, band, window=window)

        for key, actual in assigned.items():
            mismatch = ~np.isclose(actual[1:], expected[key], rtol=1e-6, atol=1e-3)
            if actual[0] != 0 or mismatch.any():
                raise ValueError(
                    f"Demand raster changed annual shape totals for {key}: "
                    f"shape IDs {shape_ids[mismatch].tolist()}, "
                    f"differences (MWh) {(actual[1:] - expected[key])[mismatch].tolist()}, "
                    f"outside shapes (MWh) {actual[0]}."
                )


if __name__ == "__main__":
    sys.stderr = open(snakemake.log[0], "w", buffering=1)
    write_heat_demand_rasters(
        pd.read_parquet(snakemake.input.annual_demand),
        gpd.read_parquet(snakemake.input.shapes),
        snakemake.input.household_space_heat,
        snakemake.input.residential_support,
        {
            "space_heat": snakemake.output.space_heat,
            "hot_water": snakemake.output.hot_water,
        },
        commercial_path=snakemake.input.commercial_space_heat,
        commercial_support_path=snakemake.input.commercial_support,
    )
