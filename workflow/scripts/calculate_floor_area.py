"""Regionalise floor-area totals with EUBUCCO-first building support.

Each complete control region and sector uses one selected source. EUBUCCO
and Microsoft support use centroid or footprint allocation. Scope clipping
preserves each contribution's regional share.

Sources:
    Floor-area regionalisation: https://doi.org/10.3390/en12244789
    Building attributes: https://docs.eubucco.com/v0.2/data-format/schema/
    Population counts: https://human-settlement.emergency.copernicus.eu/ghs_pop2023.php
"""

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import geopandas as gpd
import numpy as np
import pandas as pd
import pyarrow.dataset as ds
import rasterio
import shapely
from _building_count import building_count_grid
from _eubucco import eubucco_batch_filter, read_plan
from _floor_area import (
    allocate_building_footprints,
    filter_dominant_buildings,
    microsoft_floor_area_support,
    microsoft_population_support,
    select_building_sectors,
)
from _microsoft import low_coverage_quadkeys
from _schemas import EUBUCCO_PROCESSED_SCHEMA, validate_population_raster
from _space_heat_weight import surface_to_volume_ratio, surface_volume_power
from _utils import (
    SUPPORT_BANDS,
    SUPPORT_UNITS,
    clipped_grid,
    output_profile,
    point_grid,
    population_grid,
    write_raster,
)

if TYPE_CHECKING:
    snakemake: Any

sys.stderr = open(snakemake.log[0], "w")
microsoft_settings = snakemake.params.microsoft
plan = read_plan(snakemake.input.plan)
batch_plan = read_plan(snakemake.input.batches)
batch_regions = batch_plan["batches"][snakemake.wildcards.batch]
regions = (
    gpd.read_parquet(snakemake.input.nuts3).set_index("region_id").loc[batch_regions]
)
scope = gpd.read_parquet(snakemake.input.scope).geometry.item()
shapely.prepare(scope)
land_shapes = gpd.read_parquet(snakemake.input.shapes).to_crs(regions.crs)
country_land = land_shapes.dissolve(by="country_id").geometry
totals = pd.read_parquet(snakemake.input.totals).set_index("region_id")
count_proxies = pd.read_parquet(snakemake.input.count_proxies)
count_proxies = count_proxies.set_index("country_id")
statistics = pd.read_parquet(snakemake.input.microsoft_statistics)
low_quadkeys = low_coverage_quadkeys(
    statistics, microsoft_settings["minimum_building_count"]
)

validate_population_raster(snakemake.input.population)
population_source = rasterio.open(snakemake.input.population)

# Read overlapping legacy regions within the complete batch bounds. Clipping to
# the requested scope here would change the denominator of regional allocations.
legacy_ids = sorted(
    {
        legacy
        for region_id in batch_regions
        for legacy in plan["regions"][region_id]["eubucco_region_ids"]
    }
)
eubucco = (
    ds.dataset(snakemake.input.eubucco, format="parquet")
    .to_table(
        columns=EUBUCCO_PROCESSED_SCHEMA.names,
        filter=eubucco_batch_filter(legacy_ids, regions.total_bounds),
    )
    .to_pandas(categories=["region_id", "type", "subtype"])
)
eubucco = gpd.GeoDataFrame(
    eubucco, geometry=gpd.points_from_xy(eubucco.x, eubucco.y, crs=regions.crs)
)
# ASSUME gross building floor area equals footprint area times reported storeys.
eubucco["floor_area_m2"] = eubucco.footprint_area_m2 * eubucco.floors

microsoft = (
    ds.dataset(snakemake.input.microsoft, format="parquet")
    .to_table(filter=ds.field("region_id").isin(batch_regions))
    .to_pandas()
)
microsoft = gpd.GeoDataFrame(
    microsoft, geometry=gpd.points_from_xy(microsoft.x, microsoft.y, crs=regions.crs)
)
output_directory = Path(snakemake.output.partials)
output_directory.mkdir(parents=True, exist_ok=True)
summary_rows = []

for region_id, region in regions.iterrows():
    # First allocate over the complete census region. Clip only after each
    # sector total has been assigned to buildings or population cells.
    clipped = region.geometry.intersection(scope)
    region_totals = totals.loc[region_id]
    legacy = plan["regions"][region_id]["eubucco_region_ids"]
    # Centroid containment maps candidate buildings from source region IDs to
    # the current administrative boundaries.
    selected = eubucco.loc[
        eubucco.region_id.isin(legacy) & eubucco.geometry.within(region.geometry)
    ]
    sources = plan["regions"][region_id]
    retained = filter_dominant_buildings(
        selected, sources, snakemake.params.maximum_regional_floor_area_share, region_id
    )
    eligible = (
        retained["type"].eq("residential")
        & (sources["residential_source"] == "eubucco")
    ) | (
        retained.subtype.isin(["commercial", "public"])
        & (sources["commercial_source"] == "eubucco")
    )
    full_profile = output_profile(region.geometry.bounds, regions.crs, dtype="float64")
    selected = allocate_building_footprints(
        retained.loc[eligible],
        region.geometry,
        full_profile,
        scope,
        country_land.loc[region.country_id],
        snakemake.params.footprint_allocation_threshold_cells,
        region_id,
    )
    residential, commercial = select_building_sectors(selected)
    fallback = microsoft.loc[microsoft.region_id.eq(region_id)]
    region_low_quadkeys = low_quadkeys & set(
        plan["regions"][region_id]["microsoft_quadkeys"]
    )
    population = population_grid(
        population_source, full_profile, region.geometry, region_totals.population
    )

    uses_proxy = "microsoft" in {
        sources["residential_source"],
        sources["commercial_source"],
    }
    proxy_population = np.zeros_like(population)
    if uses_proxy:
        # Sparse coverage is classified on raw tile counts, never expanded rows.
        fallback = fallback.loc[~fallback.quadkey.isin(region_low_quadkeys)].copy()
        fallback["footprint_support_m2"] = fallback.footprint_area_m2
        microsoft_threshold = microsoft_settings["footprint_allocation_threshold_cells"]
        fallback = allocate_building_footprints(
            fallback,
            region.geometry,
            full_profile,
            scope,
            country_land.loc[region.country_id],
            microsoft_threshold,
            region_id,
            area_column="footprint_support_m2",
        )
        proxy_population = microsoft_population_support(
            full_profile,
            fallback,
            population,
            region_low_quadkeys,
            footprint_allocation=microsoft_threshold is not None,
        )

    if plan["regions"][region_id]["residential_source"] == "eubucco":
        # Preserve relative building weights while matching the residential
        # control total derived from census data or configured country proxies.
        support = residential.floor_area_m2.sum()
        if not np.isfinite(support) or support <= 0:
            raise ValueError(
                f"No usable EUBUCCO residential support for {region_id}; "
                "review building quality-control settings"
            )
        residential = residential.copy()
        residential["floor_area_m2"] *= region_totals.residential_total_m2 / support
        residential_proxy = np.zeros_like(population)
    else:
        residential, residential_proxy = microsoft_floor_area_support(
            fallback,
            proxy_population,
            region_totals.residential_total_m2,
            region_totals.population,
        )

    if plan["regions"][region_id]["commercial_source"] == "eubucco":
        # Commercial/public EUBUCCO area is retained at its observed total.
        if commercial.empty:
            raise ValueError(
                f"No EUBUCCO commercial support for {region_id}; "
                "review building quality-control settings"
            )
        commercial_proxy = np.zeros_like(population)
    else:
        assert np.isfinite(region_totals.commercial_fallback_m2)
        commercial, commercial_proxy = microsoft_floor_area_support(
            fallback,
            proxy_population,
            region_totals.commercial_fallback_m2,
            region_totals.population,
        )

    # Keep additive compactness statistics before country centring. Missing
    # observations and Microsoft support contribute to total floor area (F), but
    # not valid-observation area (V) or the area-weighted compactness sum (Q).
    if plan["regions"][region_id]["residential_source"] == "eubucco":
        settings = snakemake.params.surface_volume
        ratio = surface_to_volume_ratio(
            residential.footprint_area_m2,
            residential.height_m,
            residential.footprint_perimeter_m,
        )
        power = surface_volume_power(ratio, settings["elasticity"])
        valid = np.isfinite(power)
        residential["valid_area"] = np.where(valid, residential.floor_area_m2, 0.0)
        residential["weighted_power"] = np.where(
            valid, residential.floor_area_m2 * power, 0.0
        )
    else:
        residential["valid_area"] = 0.0
        residential["weighted_power"] = 0.0

    full_floor = (
        point_grid(full_profile, residential, residential.floor_area_m2)
        + residential_proxy
    )
    full_valid = point_grid(full_profile, residential, residential.valid_area)
    full_power = point_grid(full_profile, residential, residential.weighted_power)
    # Clip after allocation so scoped cells retain their share of regional totals.
    profile = output_profile(clipped.bounds, regions.crs, dtype="float64")
    inside = residential.loc[residential.in_scope]
    commercial_inside = commercial.loc[commercial.in_scope]
    floor = (
        point_grid(profile, inside, inside.floor_area_m2)
        + clipped_grid(residential_proxy, full_profile, profile, clipped),
        point_grid(profile, commercial_inside, commercial_inside.floor_area_m2)
        + clipped_grid(commercial_proxy, full_profile, profile, clipped),
    )
    scoped = (
        clipped_grid(population, full_profile, profile, clipped),
        point_grid(profile, inside, inside.valid_area),
        point_grid(profile, inside, inside.weighted_power),
    )
    tags = {
        "region_id": region_id,
        "residential_source": plan["regions"][region_id]["residential_source"],
        "commercial_source": plan["regions"][region_id]["commercial_source"],
        "surface_volume_elasticity": snakemake.params.surface_volume["elasticity"],
    }
    for kind, values, grid in (
        ("floor_area", floor, profile),
        (
            "residential_full",
            (full_floor, population, full_valid, full_power),
            full_profile,
        ),
        ("residential_scoped", scoped, profile),
    ):
        write_raster(
            output_directory / region_id / f"{kind}.tif",
            grid,
            values,
            SUPPORT_BANDS[kind],
            SUPPORT_UNITS[kind],
            tags,
        )
    # Counts reuse sector selections and the same population fallback grid.
    counts = building_count_grid(
        profile,
        full_profile,
        {"residential": residential, "commercial": commercial},
        sources,
        count_proxies.loc[region.country_id] if uses_proxy else None,
        proxy_population,
        clipped,
    )
    count_path = output_directory / region_id / "building_count.tif"
    write_raster(
        count_path, profile, (counts,), ("building_count",), ("buildings/ha",), tags
    )
    summary_rows.append(
        {
            "region_id": region_id,
            "country_id": region.country_id,
            "residential_source": tags["residential_source"],
            "commercial_source": tags["commercial_source"],
            "population": region_totals.population,
            "residential_floor_area_m2": region_totals.residential_total_m2,
            "commercial_floor_area_m2": commercial.floor_area_m2.sum()
            + commercial_proxy.sum(),
            "valid_floor_area_m2": residential.valid_area.sum(),
            "weighted_sv_power": residential.weighted_power.sum(),
        }
    )


pd.DataFrame(summary_rows).to_parquet(output_directory / "summary.parquet", index=False)

population_source.close()
