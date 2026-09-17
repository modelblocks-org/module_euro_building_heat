"""Calculate residential heat weights from persistent floor-area intermediates.

Complete-region arrays preserve normalization and diagnostic totals. Scoped
arrays preserve the distinct building-centroid and population-cell clipping.
"""

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd
import rasterio
from _schemas import (
    validate_conserved_total,
    validate_heat_building_support,
    validate_heat_diagnostics,
    validate_heat_tables,
)
from _space_heat_weight import blended_floor_area, weight_from_support
from _utils import SPACE_HEAT_WEIGHT_BANDS, read_region_support, write_raster

if TYPE_CHECKING:
    snakemake: Any

sys.stderr = open(snakemake.log[0], "w")
summary = pd.read_parquet(Path(snakemake.input.support) / "summary.parquet").set_index(
    "region_id"
)
age = pd.read_parquet(snakemake.input.age).set_index("region_id")
statistics = pd.read_parquet(snakemake.input.sv_statistics).set_index("country_id")
validate_heat_tables(summary, age, statistics)
output_directory = Path(snakemake.output.partials)
output_directory.mkdir(parents=True, exist_ok=True)
diagnostics = []

for region_id, row in summary.iterrows():
    floor, full, scoped, profile = read_region_support(
        Path(snakemake.input.support) / region_id
    )
    age_row = age.loc[region_id]
    eligible_population = full[1][full[0] > 0].sum()
    arguments = dict(
        reference=statistics.at[row.country_id, "sv_power_reference"],
        total=row.residential_floor_area_m2,
        population_total=eligible_population,
        share=snakemake.params.population_share,
        age=age_row.age_factor,
    )
    # Normalize on the whole region, then evaluate the same formula on the
    # clipped arrays; clipping must not redistribute the outside share.
    full_weights = weight_from_support(*full, **arguments)
    validate_conserved_total(
        blended_floor_area(
            full[0],
            full[1],
            row.residential_floor_area_m2,
            eligible_population,
            snakemake.params.population_share,
        ).sum(),
        row.residential_floor_area_m2,
    )
    weights = weight_from_support(floor[0], *scoped, **arguments)
    raster_path = output_directory / f"{region_id}.tif"
    write_raster(
        raster_path,
        {**profile, "dtype": snakemake.params.raster["dtype"]},
        (weights,),
        SPACE_HEAT_WEIGHT_BANDS,
        ("weighted_m2/ha",),
        {
            "region_id": region_id,
            "method": "blended_floor_area * surface_volume * age",
            "population_share": snakemake.params.population_share,
            "eubucco_population_support": snakemake.params.eubucco_support,
            "microsoft_population_fallback": snakemake.params.microsoft_population_fallback,
        },
    )

    with (
        rasterio.open(raster_path) as heat,
        rasterio.open(
            Path(snakemake.input.support) / region_id / "building_count.tif"
        ) as counts,
        rasterio.open(
            Path(snakemake.input.support) / region_id / "floor_area.tif"
        ) as floor_raster,
    ):
        validate_heat_building_support(heat, counts, floor_raster)

    diagnostics.append(
        {
            "country_id": row.country_id,
            "region_id": region_id,
            "residential_floor_area_m2": row.residential_floor_area_m2,
            "residential_source": row.residential_source,
            "sv_valid_floor_area_fraction": row.valid_floor_area_m2
            / row.residential_floor_area_m2
            if row.residential_floor_area_m2 > 0
            else 0.0,
            "age_data_available": age_row.age_data_available,
            "age_coverage_fraction": age_row.coverage_fraction,
            "raw_age_factor": age_row.age_factor_raw,
            "normalised_age_factor": age_row.age_factor,
            "raw_heat_weight": full_weights.sum(),
            "eligible_population": eligible_population,
            "excluded_population": full[1][full[0] == 0].sum(),
        }
    )

path = output_directory / "diagnostics.parquet"
diagnostics = pd.DataFrame(diagnostics)
validate_heat_diagnostics(diagnostics)
diagnostics.to_parquet(path, index=False)
