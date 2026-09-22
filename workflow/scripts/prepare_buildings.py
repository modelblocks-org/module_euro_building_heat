"""Prepare geography, source plans, control totals and heat-weight references.

Each function is a separate Snakemake job selected by params.step. Keeping
entry points together does not serialize independent branches of the DAG.
Source metadata are checked on ingestion; generated artifacts are read directly.

Sources:
    Census definitions: https://ec.europa.eu/eurostat/cache/metadata/en/cens_21_esms.htm
    EUBUCCO fields: https://docs.eubucco.com/v0.2/data-format/schema/
    Microsoft tiles: https://github.com/microsoft/GlobalMLBuildingFootprints
    Regionalisation method: https://doi.org/10.3390/en12244789

Proxy countries, class representatives and multipliers are workflow assumptions
in config/config.yaml; the source datasets do not prescribe those values.
"""

import json
import sys
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Any

import geopandas as gpd
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from _eubucco import EUBUCCO_SCHEMA, assign_region_batches, map_regions, read_plan
from _microsoft import (
    MICROSOFT_SCHEMA,
    MICROSOFT_TILE_STATISTICS_SCHEMA,
    MICROSOFT_TOTALS_SCHEMA,
    intersecting_quadkeys,
)
from _schemas import validate_population_summaries
from _utils import population_summaries, processing_crs


def building_sources(snakemake):
    """Choose building sources per control region and sector for one shape case.

    Positive floor-area sums in intersecting legacy regions select EUBUCCO
    independently for residential and commercial/public buildings. Otherwise
    select Microsoft tiles, requiring a configured proxy country. Write the
    source manifest and empty tables for branches that do not need a source.
    """
    regions = gpd.read_parquet(snakemake.input.regions)
    links = pd.read_csv(snakemake.input.microsoft_index, dtype={"QuadKey": str})
    available_quadkeys = set(links.QuadKey)
    # Metadata are cached and validated by independent download rules.
    eubucco_regions = gpd.read_parquet(snakemake.input.eubucco_nuts).to_crs(regions.crs)
    stats = gpd.read_parquet(snakemake.input.eubucco_stats)
    mapping = map_regions(regions, eubucco_regions, stats.region_id)

    stats = stats.set_index("region_id")
    region_plan = {}
    for row in regions.to_crs(4326).itertuples():
        eubucco = mapping[row.region_id]
        selected = stats.reindex(eubucco["region_ids"])
        # Metadata determine source availability before downloading buildings.
        # Commercial support includes both commercial and public subtypes.
        residential = selected.floor_area_type_residential.sum() > 0
        commercial = (
            selected.floor_area_subtype_commercial.sum()
            + selected.floor_area_subtype_public.sum()
            > 0
        )
        microsoft = not residential or not commercial
        if microsoft:
            assert row.country_id in snakemake.params.proxies["countries"]
        region_plan[row.region_id] = {
            "eubucco_region_ids": eubucco["region_ids"],
            "eubucco_nuts2_ids": eubucco["nuts2_ids"],
            "residential_source": "eubucco" if residential else "microsoft",
            "commercial_source": "eubucco" if commercial else "microsoft",
            "microsoft_quadkeys": sorted(
                set(intersecting_quadkeys(row.geometry)) & available_quadkeys
            )
            if microsoft
            else [],
        }

    plan = {
        "schema_version": 2,
        "eubucco_source": snakemake.params.eubucco["source"],
        "microsoft_release": snakemake.params.microsoft["release"],
        "crs": regions.crs.to_string(),
        "regions": region_plan,
    }
    Path(snakemake.output.manifest).parent.mkdir(parents=True, exist_ok=True)
    with open(snakemake.output.manifest, "w") as stream:
        json.dump(plan, stream, indent=2)
    # Downstream rules can read a consistent schema even for an unused source.
    pq.write_table(
        pa.Table.from_batches([], schema=EUBUCCO_SCHEMA), snakemake.output.empty_eubucco
    )
    pq.write_table(
        pa.Table.from_batches([], schema=MICROSOFT_SCHEMA),
        snakemake.output.empty_microsoft,
    )
    pq.write_table(
        pa.Table.from_batches([], schema=MICROSOFT_TILE_STATISTICS_SCHEMA),
        snakemake.output.empty_microsoft_statistics,
    )

    pq.write_table(
        pa.Table.from_batches([], schema=MICROSOFT_TOTALS_SCHEMA),
        snakemake.output.empty_microsoft_totals,
    )


def floor_area_batches(snakemake):
    """Write a batch manifest balanced by legacy EUBUCCO building counts.

    Keep current NUTS-2 groups together to reuse overlapping legacy selections.
    Batch entries contain complete control regions, including shape fallbacks.
    """
    eubucco = read_plan(snakemake.input.plan)
    stats = gpd.read_parquet(snakemake.input.stats)
    plan = {
        "schema_version": 1,
        "batches": assign_region_batches(
            eubucco["regions"], stats, snakemake.params.batch_count
        ),
    }
    Path(snakemake.output.manifest).parent.mkdir(parents=True, exist_ok=True)
    with open(snakemake.output.manifest, "w") as stream:
        json.dump(plan, stream, indent=2)


def building_population(snakemake):
    """Cache native-grid population for each distinct control/reference geometry."""
    regions = gpd.read_parquet(snakemake.input.regions)
    references = {
        reference
        for country in regions.country_id.unique()
        for reference in snakemake.params.proxies["countries"].get(country, [])
    }
    reverse = {
        code: country for country, code in snakemake.params.country_codes.items()
    }
    floor = gpd.read_file(snakemake.input.nuts3_source).to_crs(regions.crs)
    floor["country_id"] = floor.CNTR_CODE.map(reverse)
    floor = floor.loc[floor.country_id.isin(references)].rename(
        columns={"NUTS_ID": "region_id"}
    )
    counts = gpd.read_parquet(snakemake.input.stats)
    counts["country_id"] = counts.country.map(reverse)
    counts = counts.loc[counts.country_id.isin(references)]
    table = population_summaries(
        snakemake.input.population,
        {"regions": regions, "floor_reference": floor, "count_reference": counts},
        snakemake.params.population["chunk_size"],
    )
    validate_population_summaries(table)
    Path(snakemake.output.table).parent.mkdir(parents=True, exist_ok=True)
    table.to_parquet(snakemake.output.table, index=False)


def floor_area_totals(snakemake):
    """Prepare residential and commercial/public control-region floor-area totals.

    Eurostat residential totals remain authoritative. Countries without totals use
    reference-country floor area per inhabitant, while Microsoft footprint area and
    reference-country mean floors distribute that total over their actual buildings.
    """
    from _floor_area import census_values, residential_floor_area

    eurostat = snakemake.params.eurostat
    eubucco = snakemake.params.eubucco
    proxies = snakemake.params.proxies
    countries = snakemake.params.country_codes
    reverse_countries = {code: country for country, code in countries.items()}
    regions = gpd.read_parquet(snakemake.input.nuts3).set_index("region_id")
    nuts_source = gpd.read_file(snakemake.input.nuts3_source).to_crs(regions.crs)
    nuts_source["country_id"] = nuts_source.CNTR_CODE.map(reverse_countries)
    nuts_source = nuts_source.set_index("NUTS_ID")

    population = pd.read_parquet(snakemake.input.population_summaries)
    population = population.set_index(["population_kind", "region_id"]).population
    regional_population = population.loc["regions"].reindex(regions.index)
    raw_census = census_values(snakemake.input.census)
    census_area = residential_floor_area(raw_census, eurostat)
    stats = gpd.read_parquet(snakemake.input.eubucco_stats)
    plan = read_plan(snakemake.input.plan)
    microsoft_area = (
        pd.read_parquet(snakemake.input.microsoft_totals)
        .set_index("region_id")
        .footprint_area_m2.reindex(regions.index, fill_value=0)
    )

    def reference_mean_floors(reference_countries):
        """Average country mean storeys using configured floor-bin representatives.

        Building counts weight bins within each country; reference countries
        then receive equal weight regardless of their building-stock size.
        """
        values = []
        representatives = pd.Series(eubucco["floor_bin_representatives"])
        for country in reference_countries:
            selected = stats.loc[stats.country.eq(countries[country])]
            counts = selected[representatives.index].sum()
            assert counts.sum() > 0
            values.append(counts.dot(representatives) / counts.sum())
        return np.mean(values)

    def reference_sector_shares(reference_countries):
        """Average reference-country residential and commercial/public area shares.

        Shares use only these two sectors, excluding other building types.
        Countries receive equal weight, rather than pooling their floor areas.
        """
        values = []
        for country in reference_countries:
            selected = stats.loc[stats.country.eq(countries[country])]
            residential = selected.floor_area_type_residential.sum()
            commercial = (
                selected.floor_area_subtype_commercial.sum()
                + selected.floor_area_subtype_public.sum()
            )
            assert residential > 0
            assert commercial > 0
            values.append(residential / (residential + commercial))
        residential = float(np.mean(values))
        return residential, 1 - residential

    def reference_floor_area_per_inhabitant(reference_countries):
        """Return the equal-weight processed Eurostat floor area per inhabitant."""
        values = []
        for country in reference_countries:
            valid = census_area.index.intersection(
                nuts_source.index[nuts_source.country_id.eq(country)]
            )
            valid = valid[census_area.reindex(valid).notna()]
            inhabitants = population.loc["floor_reference"].loc[valid].sum()
            assert census_area.loc[valid].sum() > 0
            assert inhabitants > 0
            values.append(census_area.loc[valid].sum() / inhabitants)
        return np.mean(values)

    residential_totals = census_area.reindex(regions.index)
    commercial_totals = pd.Series(np.nan, index=regions.index)
    for region_id, region in regions.iterrows():
        source = plan["regions"][region_id]
        if "microsoft" not in {
            source["residential_source"],
            source["commercial_source"],
        }:
            continue
        references = proxies["countries"][region.country_id]
        residential_share, commercial_share = reference_sector_shares(references)
        if pd.isna(residential_totals[region_id]):
            total = microsoft_area[region_id] * reference_mean_floors(references)
            residential_totals[region_id] = total * residential_share
            commercial_totals[region_id] = total * commercial_share
        else:
            commercial_totals[region_id] = (
                residential_totals[region_id] * commercial_share / residential_share
            )

    # Scale Microsoft building support for each proxied country to the residential
    # Eurostat floor area per inhabitant calculated from its reference countries.
    missing_census = census_area.reindex(regions.index).isna()
    for country in regions.loc[missing_census].country_id.unique():
        selected = regions.country_id.eq(country) & missing_census
        microsoft = selected & pd.Series(
            {
                region_id: plan["regions"][region_id]["residential_source"]
                == "microsoft"
                for region_id in regions.index
            }
        )
        if microsoft.any():
            target = (
                reference_floor_area_per_inhabitant(proxies["countries"][country])
                * regional_population.loc[microsoft].sum()
            )
            scale = target / residential_totals.loc[microsoft].sum()
            residential_totals.loc[microsoft] *= scale
            commercial_totals.loc[microsoft] *= scale

    # EUBUCCO-backed regions use the same shared reference-country intensity.
    for region_id in residential_totals.index[residential_totals.isna()]:
        region = regions.loc[region_id]
        residential_totals[region_id] = (
            reference_floor_area_per_inhabitant(proxies["countries"][region.country_id])
            * regional_population[region_id]
        )

    totals = pd.DataFrame(
        {
            "region_id": regions.index,
            "country_id": regions.country_id,
            "population": regional_population,
            "residential_total_m2": residential_totals,
            "commercial_fallback_m2": commercial_totals,
        }
    ).reset_index(drop=True)
    Path(snakemake.output.table).parent.mkdir(parents=True, exist_ok=True)
    totals.to_parquet(snakemake.output.table, index=False)


def nuts3(snakemake):
    """Prepare the current NUTS-3 geography for one user-defined shape case.

    The script selects official NUTS-3 polygons with positive-area overlap in land
    shapes belonging to the same country. This prevents generalized borders from
    introducing regions from neighbouring countries.
    Countries outside the NUTS geography receive one shape-based region so their
    configured floor-area proxies remain reachable.
    The complete NUTS geometry is retained because regional census totals and
    building-stock totals refer to administrative regions, not clipped fragments.
    Clipping occurs only when the final hectare rasters are written.

    Source:
        Eurostat GISCO NUTS: https://ec.europa.eu/eurostat/web/gisco/geodata/statistical-units/territorial-units-statistics
    """
    shapes = gpd.read_parquet(snakemake.input.shapes)
    nuts3 = gpd.read_file(snakemake.input.nuts3)
    working_crs = processing_crs(shapes)
    shapes = shapes.to_crs(nuts3.crs)
    scope = shapes.geometry.union_all()
    nuts3["country_id"] = nuts3.CNTR_CODE.map(
        {code: country for country, code in snakemake.params.country_codes.items()}
    )
    # Require positive-area overlap: boundary touches do not create usable regions.
    nuts3 = nuts3.loc[nuts3.intersects(scope), ["NUTS_ID", "country_id", "geometry"]]
    nuts3 = nuts3.loc[nuts3.geometry.intersection(scope).area.gt(0)].rename(
        columns={"NUTS_ID": "region_id"}
    )
    nuts3.geometry = nuts3.geometry.make_valid()

    matches = gpd.overlay(
        nuts3,
        shapes[["shape_id", "country_id", "geometry"]].rename(
            columns={"country_id": "shape_country_id"}
        ),
        how="intersection",
        keep_geom_type=False,
    )
    matches = matches.loc[matches.country_id.eq(matches.shape_country_id)]
    matched_shapes = set(matches.shape_id)
    matches["area"] = matches.area
    matches = matches.sort_values("area").drop_duplicates("region_id", keep="last")
    nuts3 = nuts3.loc[nuts3.region_id.isin(matches.region_id)]

    # NUTS omissions and countries outside Europe use the user shapes as control
    # regions. Their assumption-based totals remain tied to the supplied geography.
    fallback = shapes.loc[~shapes.shape_id.isin(matched_shapes)].dissolve(
        ["country_id", "shape_id"], as_index=False
    )
    fallback["region_id"] = fallback.shape_id.map(
        lambda value: "shape-" + sha256(value.encode()).hexdigest()[:16]
    )
    nuts3 = gpd.GeoDataFrame(
        pd.concat(
            [
                nuts3[["region_id", "country_id", "geometry"]],
                fallback[["region_id", "country_id", "geometry"]],
            ],
            ignore_index=True,
        ),
        crs=nuts3.crs,
    ).to_crs(working_crs)
    Path(snakemake.output.regions).parent.mkdir(parents=True, exist_ok=True)
    nuts3.to_parquet(snakemake.output.regions, index=False)


def nuts3_building_age(snakemake):
    """Prepare observed and country-centred NUTS-3 residential age factors.

    The Eurostat 2021 dwelling construction-period table is used only where a
    region reports known-period conventional dwellings. Unknown periods are
    excluded. The source's combined 1981--2000 bin uses its explicit configured
    multiplier; no missing region borrows another region's age composition.
    """
    from _floor_area import census_values

    settings = snakemake.params.settings
    regions = gpd.read_parquet(snakemake.input.nuts3).set_index("region_id")
    totals = pd.read_parquet(snakemake.input.floor_area).set_index("region_id")
    raw = census_values(snakemake.input.census)
    raw = raw.loc[raw.freq.eq("A") & raw.housing.eq("DW") & raw.unit.eq("NR")]

    multipliers = settings["multipliers"]
    period_multipliers = {
        "Y_LT1919": multipliers["before_1991"],
        "Y1919-1945": multipliers["before_1991"],
        "Y1946-1960": multipliers["before_1991"],
        "Y1961-1980": multipliers["before_1991"],
        "Y1981-2000": settings["cutoff_spanning_bin_multipliers"]["Y1981-2000"],
        "Y2001-2010": multipliers["after_2000"],
        "Y2011-2015": multipliers["after_2000"],
        "Y_GE2016": multipliers["after_2000"],
    }
    known_rows = raw.loc[raw.y_const.isin(period_multipliers)].copy()
    known_rows["weighted"] = known_rows.value * known_rows.y_const.map(
        period_multipliers
    )
    known = (
        known_rows.groupby("geo")
        .value.sum(min_count=1)
        .reindex(regions.index)
        .fillna(0)
    )
    weighted = (
        known_rows.groupby("geo")
        .weighted.sum(min_count=1)
        .reindex(regions.index)
        .fillna(0)
    )
    total = (
        raw.loc[raw.y_const.eq("TOTAL")]
        .groupby("geo")
        .value.sum(min_count=1)
        .reindex(regions.index)
        .fillna(0)
    )
    available = known.gt(0)
    factor_raw = weighted.div(known).where(available)
    coverage = known.div(total).where(total.gt(0), 0)

    # Centre observed age factors on the country mean, weighted by residential
    # floor area. Regions without observations keep a neutral factor of one.
    references = (
        pd.DataFrame(
            {
                "country_id": regions.country_id,
                "floor_area": totals.residential_total_m2,
                "weighted": totals.residential_total_m2 * factor_raw,
            }
        )
        .loc[available]
        .groupby("country_id")[["floor_area", "weighted"]]
        .sum()
    )
    references = references.weighted.div(references.floor_area)
    factor = factor_raw.div(regions.country_id.map(references)).where(available, 1.0)

    age = pd.DataFrame(
        {
            "region_id": regions.index,
            "country_id": regions.country_id,
            "age_factor_raw": factor_raw,
            "age_factor": factor,
            "age_data_available": available,
            "known_dwellings": known,
            "total_dwellings": total,
            "coverage_fraction": coverage,
        }
    ).reset_index(drop=True)
    Path(snakemake.output.table).parent.mkdir(parents=True, exist_ok=True)
    age.to_parquet(snakemake.output.table, index=False)


def space_heat_sv_statistics(snakemake):
    """Aggregate cached complete-region compactness sums without rereading buildings."""
    summary = pd.concat(
        [
            pd.read_parquet(Path(path) / "summary.parquet")
            for path in snakemake.input.support
        ],
        ignore_index=True,
    )
    statistics = summary.groupby("country_id")[
        ["valid_floor_area_m2", "weighted_sv_power"]
    ].sum()
    # Divide additive country sums, not region means: each observed square
    # metre has equal weight. Countries without observations remain neutral.
    statistics["sv_power_reference"] = statistics.weighted_sv_power.div(
        statistics.valid_floor_area_m2
    ).where(statistics.valid_floor_area_m2.gt(0), 1.0)
    Path(snakemake.output.table).parent.mkdir(parents=True, exist_ok=True)
    statistics.reset_index().to_parquet(snakemake.output.table, index=False)


if __name__ == "__main__":
    if TYPE_CHECKING:
        snakemake: Any
    sys.stderr = open(snakemake.log[0], "w")
    steps = {
        "building_sources": building_sources,
        "floor_area_batches": floor_area_batches,
        "building_population": building_population,
        "floor_area_totals": floor_area_totals,
        "nuts3": nuts3,
        "nuts3_building_age": nuts3_building_age,
        "space_heat_sv_statistics": space_heat_sv_statistics,
    }
    steps[snakemake.params.step](snakemake)
