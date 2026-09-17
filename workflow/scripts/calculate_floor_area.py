"""Regionalise floor-area totals with EUBUCCO-first building support.

Each complete control region and sector uses one source. EUBUCCO floor area is
preferred; Microsoft footprint area is the fallback weight. The requested
shape receives only the share represented by building centroids and population
cell centres inside it.

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
from _eubucco import EUBUCCO_COLUMNS, eubucco_batch_filter, read_plan
from _floor_area import (
    microsoft_floor_area_support,
    microsoft_population_support,
    select_building_sectors,
)
from _microsoft import low_coverage_quadkeys
from _schemas import (
    validate_building_count_proxies,
    validate_conserved_total,
    validate_sector_count_ratios,
)
from _space_heat_weight import surface_to_volume_ratio, surface_volume_power
from _utils import (
    SUPPORT_BANDS,
    SUPPORT_UNITS,
    clipped_grid,
    output_profile,
    point_grid,
    points_within_scope,
    population_grid,
    write_raster,
)

if TYPE_CHECKING:
    snakemake: Any

sys.stderr = open(snakemake.log[0], "w")
eubucco_settings = snakemake.params.eubucco
microsoft_settings = snakemake.params.microsoft
plan = read_plan(snakemake.input.plan)
batch_plan = read_plan(snakemake.input.batches)
batch_regions = batch_plan["batches"][snakemake.wildcards.batch]
regions = (
    gpd.read_parquet(snakemake.input.nuts3).set_index("region_id").loc[batch_regions]
)
scope = gpd.read_parquet(snakemake.input.scope).geometry.item()
shapely.prepare(scope)
totals = pd.read_parquet(snakemake.input.totals).set_index("region_id")
count_proxies = pd.read_parquet(snakemake.input.count_proxies)
validate_building_count_proxies(count_proxies)
count_proxies = count_proxies.set_index("country_id")
statistics = pd.read_parquet(snakemake.input.microsoft_statistics)
low_quadkeys = low_coverage_quadkeys(
    statistics, microsoft_settings["minimum_building_count"]
)

population_source = rasterio.open(snakemake.input.population)

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
        columns=EUBUCCO_COLUMNS[1:],
        filter=eubucco_batch_filter(legacy_ids, regions.total_bounds),
    )
    .to_pandas(categories=["region_id", "type", "subtype"])
)
eubucco = gpd.GeoDataFrame(
    eubucco, geometry=gpd.points_from_xy(eubucco.x, eubucco.y, crs=regions.crs)
)
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
    selected = eubucco.loc[
        eubucco.region_id.isin(legacy) & eubucco.geometry.within(region.geometry)
    ]
    residential, commercial = select_building_sectors(
        selected,
        eubucco_settings["residential_type"],
        eubucco_settings["commercial_subtypes"],
    )
    fallback = microsoft.loc[microsoft.region_id.eq(region_id)]
    full_profile = output_profile(
        region.geometry.bounds, snakemake.params.raster, regions.crs
    )
    region_low_quadkeys = low_quadkeys & set(
        plan["regions"][region_id]["microsoft_quadkeys"]
    )
    population = population_grid(
        population_source,
        full_profile,
        region.geometry,
        snakemake.params.population_resampling,
        region_totals.population,
    )

    sources = plan["regions"][region_id]
    uses_proxy = "microsoft" in {
        sources["residential_source"],
        sources["commercial_source"],
    }
    proxy_population = np.zeros_like(population)
    if uses_proxy:
        fallback, proxy_population = microsoft_population_support(
            full_profile, fallback, population, region_low_quadkeys
        )

    if plan["regions"][region_id]["residential_source"] == "eubucco":
        support = residential.floor_area_m2.sum()
        assert support > 0
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
        assert not commercial.empty
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
    # observations and Microsoft support contribute to F but not V or Q.
    if plan["regions"][region_id]["residential_source"] == "eubucco":
        settings = snakemake.params.surface_volume
        ratio = surface_to_volume_ratio(
            residential.footprint_area_m2,
            residential.height_m,
            residential.footprint_perimeter_m,
            method=settings["method"],
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
    validate_conserved_total(full_floor.sum(), region_totals.residential_total_m2)
    full_valid = point_grid(full_profile, residential, residential.valid_area)
    full_power = point_grid(full_profile, residential, residential.weighted_power)
    profile = output_profile(clipped.bounds, snakemake.params.raster, regions.crs)
    inside = residential.loc[points_within_scope(residential, scope)]
    commercial_inside = commercial.loc[points_within_scope(commercial, scope)]
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
        "microsoft_population_fallback": microsoft_settings["population_fallback"],
        "residential_source": plan["regions"][region_id]["residential_source"],
        "commercial_source": plan["regions"][region_id]["commercial_source"],
        "surface_volume_method": snakemake.params.surface_volume["method"],
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
    for sector, buildings, proxy in (
        ("residential", residential, residential_proxy),
        ("commercial", commercial, commercial_proxy),
    ):
        if sources[f"{sector}_source"] == "microsoft":
            validate_sector_count_ratios(
                buildings, proxy, count_proxies.loc[region.country_id], sector
            )
    counts = building_count_grid(
        profile,
        full_profile,
        {"residential": residential, "commercial": commercial},
        sources,
        count_proxies.loc[region.country_id] if uses_proxy else None,
        proxy_population,
        scope,
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
