"""Merge regional building counts or space-heating support into final rasters.

``params.mode`` selects building counts or structural support. The batch plan
maps directories to region IDs. Input rasters must share the plan CRS and
grid alignment because merging adds their values without reprojection.
"""

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import _plots
import geopandas as gpd
import pandas as pd
import rasterio
from _eubucco import read_plan
from _utils import SPACE_HEAT_WEIGHT_BANDS, add_partial, output_profile

if TYPE_CHECKING:
    snakemake: Any

sys.stderr = open(snakemake.log[0], "w")
plan = read_plan(snakemake.input.plan)
batches = read_plan(snakemake.input.batches)["batches"]
shapes = gpd.read_parquet(snakemake.input.shapes).to_crs(plan["crs"])
# Use the same grid alignment as the regional inputs so they can be added
# directly into windows of the final raster.
profile = output_profile(shapes.total_bounds, plan["crs"], count=1)
# Resolve inputs by batch ID rather than relying on Snakemake input ordering.
support = {Path(path).name: Path(path) for path in snakemake.input.support}
for path in snakemake.output:
    Path(path).parent.mkdir(parents=True, exist_ok=True)

if snakemake.params.mode == "counts":
    population = snakemake.params.population
    # Read/write mode lets add_partial accumulate overlapping contributions
    # block by block without loading the full output raster into memory.
    with rasterio.open(snakemake.output.building_count, "w+", **profile) as counts:
        for batch, regions in batches.items():
            for region_id in regions:
                with rasterio.open(
                    support[batch] / region_id / "building_count.tif"
                ) as partial:
                    add_partial(counts, partial, (1,))
        counts.set_band_description(1, "building_count")
        counts.set_band_unit(1, "buildings/ha")
        counts.update_tags(
            sectors="residential,commercial,public",
            microsoft_release=plan["microsoft_release"],
            microsoft_minimum_building_count=snakemake.params.microsoft[
                "minimum_building_count"
            ],
            ghsl_epoch=population["epoch"],
        )
else:
    settings = snakemake.params.space_heat_weight
    weights = {Path(path).name: Path(path) for path in snakemake.input.weights}
    diagnostics = []
    with (
        rasterio.open(
            snakemake.output.residential_space_heat_weight, "w+", **profile
        ) as heat,
        rasterio.open(
            snakemake.output.commercial_space_heat_weight, "w+", **profile
        ) as commercial,
    ):
        for batch, regions in batches.items():
            diagnostics.append(pd.read_parquet(weights[batch] / "diagnostics.parquet"))
            for region_id in regions:
                with (
                    rasterio.open(weights[batch] / f"{region_id}.tif") as partial,
                    rasterio.open(
                        support[batch] / region_id / "floor_area.tif"
                    ) as floor,
                ):
                    # Residential weights already include the population blend,
                    # surface-to-volume adjustment and building-age factors.
                    add_partial(heat, partial, (1,))
                    # Commercial/public support uses unweighted floor area.
                    add_partial(commercial, floor, (1,), source_bands=(2,))
        heat.set_band_description(1, SPACE_HEAT_WEIGHT_BANDS[0])
        heat.set_band_unit(1, "weighted_m2/ha")
        heat.update_tags(
            method="blended_floor_area * surface_volume * age",
            population_source="GHS-POP",
            population_epoch=snakemake.params.population["epoch"],
            population_share=settings["population"]["share"],
            microsoft_minimum_building_count=snakemake.params.microsoft[
                "minimum_building_count"
            ],
            surface_volume_elasticity=settings["surface_volume"]["elasticity"],
            age_old_factor=settings["age"]["multipliers"]["before_1981"],
            age_new_factor=settings["age"]["multipliers"]["after_2000"],
            age_1981_2000_factor=settings["age"]["multipliers"]["1981_2000"],
        )

        commercial.set_band_description(1, "commercial_space_heat_weight")
        commercial.set_band_unit(1, "m2/ha")
        commercial.update_tags(method="floor_area", sectors="commercial,public")
    # Sort diagnostics geographically for consistent output across batch layouts.
    diagnostics = pd.concat(diagnostics, ignore_index=True).sort_values(
        ["country_id", "region_id"]
    )
    diagnostics.to_parquet(snakemake.output.diagnostics, index=False)


plots = (
    [
        (
            "building_count",
            "Residential, commercial and public buildings",
            "Buildings per hectare",
        )
    ]
    if snakemake.params.mode == "counts"
    else [
        (
            "residential_space_heat_weight",
            "Residential space-heating support",
            "Space-heating support (weighted m²/ha)",
        ),
        (
            "commercial_space_heat_weight",
            "Commercial and public floor area",
            "Floor area (m²/ha)",
        ),
    ]
)
for dataset, title, unit in plots:
    _plots.plot_floor_area(
        snakemake.output[dataset],
        1,
        title,
        snakemake.output[f"{dataset}_plot"],
        shapes,
        unit,
    )
