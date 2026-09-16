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


def population_blocks(reference, population, shapes: gpd.GeoDataFrame) -> Iterator:
    """Read population support and pixel-centre shape IDs in bounded windows."""
    spatial_index = shapes.sindex
    for _, window in reference.block_windows(1):
        bounds = box(*rasterio.windows.bounds(window, reference.transform))
        indices = np.sort(spatial_index.query(bounds, predicate="intersects"))
        size = (int(window.height), int(window.width))
        labels = np.zeros(size, dtype="int32")
        if len(indices):
            rasterize(
                [(shapes.geometry.iloc[i], int(i + 1)) for i in indices],
                out=labels,
                transform=reference.window_transform(window),
                all_touched=False,
            )
        values = population.read(1, window=window, masked=True).filled(0)
        if not np.isfinite(values).all() or (values < 0).any():
            raise ValueError("Population support must be finite and non-negative.")
        values[labels == 0] = 0
        yield window, values, labels


def household_block(household, band: int, window) -> np.ndarray:
    """Read finite, non-negative household support, treating nodata as zero."""
    values = household.read(band, window=window, masked=True).filled(0)
    if not np.isfinite(values).all() or (values < 0).any():
        raise ValueError(
            "Household space heat support must be finite and non-negative."
        )
    return values


def write_heat_demand_rasters(
    annual_demand: pd.DataFrame,
    shapes: gpd.GeoDataFrame,
    household_path: str,
    population_path: str,
    output_paths: dict[str, str],
) -> None:
    """Preserve annual shape totals while allocating both sinks to 100 m cells.

    Household space heat retains the structural/HDD pattern, normalised to the
    annual total per shape because upstream weather/shape intersections can
    assign boundary cells differently. Commercial space heat and all hot water
    use resampled population, normalised per shape.
    Each band inherits its demand/weather year pair from the household raster.
    """
    with ExitStack() as stack:
        household = stack.enter_context(rasterio.open(household_path))
        source_population = stack.enter_context(rasterio.open(population_path))
        population = stack.enter_context(
            WarpedVRT(
                source_population,
                crs=household.crs,
                transform=household.transform,
                width=household.width,
                height=household.height,
                resampling=Resampling.average,
                dtype="float64",
                nodata=0,
            )
        )
        shapes = shapes.to_crs(household.crs).reset_index(drop=True)
        shape_ids = pd.Index(shapes.shape_id)
        if shape_ids.has_duplicates:
            raise ValueError("Shapes contain duplicate shape IDs.")
        count = len(shapes) + 1
        totals = np.zeros(count)
        household_totals = (
            {band: np.zeros(count) for band in household.indexes}
            if "space_heat" in output_paths
            else {}
        )
        for window, values, labels in population_blocks(household, population, shapes):
            totals += np.bincount(
                labels.ravel(), weights=values.ravel(), minlength=count
            )
            for band, band_totals in household_totals.items():
                band_totals += np.bincount(
                    labels.ravel(),
                    weights=household_block(household, band, window).ravel(),
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
                    "household space heat: structural support * HDD^elasticity "
                    "normalised per shape; "
                    "other demand: average-resampled population normalised per shape"
                ),
            )
            outputs[sink] = output

        factors = {}
        household_factors = {}
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
                if sink == "space_heat":
                    household_energy = (
                        selected.loc[selected.category == "household"]
                        .groupby("shape_id")
                        .heat_demand_twh.sum()
                        .reindex(shape_ids)
                        .to_numpy()
                        * 1e6
                    )
                    if (
                        not np.isfinite(household_energy).all()
                        or (household_energy < 0).any()
                    ):
                        raise ValueError(
                            f"Invalid or missing household space heat demand for {demand_year}."
                        )
                    support = household_totals[band][1:]
                    missing = (support <= 0) & (household_energy > 0)
                    if missing.any():
                        raise ValueError(
                            "No household space heat support on output grid for "
                            f"{shape_ids[missing].tolist()}."
                        )
                    scale = np.zeros(count)
                    np.divide(
                        household_energy, support, out=scale[1:], where=support > 0
                    )
                    household_factors[band] = scale
                    selected = selected.loc[selected.category == "commercial"]
                energy = (
                    selected.groupby("shape_id")
                    .heat_demand_twh.sum()
                    .reindex(shape_ids)
                    .to_numpy()
                    * 1e6
                )
                if not np.isfinite(energy).all() or (energy < 0).any():
                    raise ValueError(
                        f"Invalid or missing {sink} demand for {demand_year}."
                    )
                missing = (totals[1:] <= 0) & (energy > 0)
                if missing.any():
                    raise ValueError(
                        f"No population support on output grid for {shape_ids[missing].tolist()}."
                    )
                scale = np.zeros(count)
                np.divide(energy, totals[1:], out=scale[1:], where=totals[1:] > 0)
                factors[band, sink] = scale
                output.set_band_description(
                    band, f"{sink}_{demand_year}_weather_{tags['weather_year']}"
                )
                output.set_band_unit(band, "MWh/cell")
                output.update_tags(band, **tags)

        assigned = {key: np.zeros(count) for key in factors}
        for window, values, labels in population_blocks(household, population, shapes):
            for band in household.indexes:
                for sink, output in outputs.items():
                    energy = values * factors[band, sink][labels]
                    if sink == "space_heat":
                        energy += (
                            household_block(household, band, window)
                            * household_factors[band][labels]
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
        snakemake.input.population,
        {
            "space_heat": snakemake.output.space_heat,
            "hot_water": snakemake.output.hot_water,
        },
    )
