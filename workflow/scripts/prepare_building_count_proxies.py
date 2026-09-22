"""Prepare country-level building-count proxies for Microsoft-sourced regions.

Run by Snakemake using the building-source plan, region metadata, EUBUCCO
statistics and precomputed population summaries. For each country needing
Microsoft buildings in either sector, average the count ratios of its
configured reference countries with equal weight per reference country.

The output Parquet table contains one row per target country: residential and
commercial/public shares of all buildings, and sector building counts per
inhabitant within the EUBUCCO statistics coverage. Downstream raster creation
uses the shares to assign sectors to Microsoft footprints and the per-person
ratios for the population fallback where footprints are sparse.
"""

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import geopandas as gpd
import pandas as pd
from _building_count import reference_count_ratios
from _eubucco import read_plan
from _schemas import EubuccoStatsSchema

if TYPE_CHECKING:
    snakemake: Any

sys.stderr = open(snakemake.log[0], "w")
plan = read_plan(snakemake.input.plan)
stats = EubuccoStatsSchema.validate(
    gpd.read_parquet(
        snakemake.input.stats, columns=list(EubuccoStatsSchema.to_schema().columns)
    )
)
regions = gpd.read_parquet(snakemake.input.regions).set_index("region_id")
# Countries using only EUBUCCO do not need proxy ratios. A Microsoft source
# for either residential or commercial buildings is enough to include one.
countries = sorted(
    {
        regions.at[region_id, "country_id"]
        for region_id, region in plan["regions"].items()
        if "microsoft" in {region["residential_source"], region["commercial_source"]}
    }
)
proxies = snakemake.params.proxies
# Compute each reference country's ratios once, even if several target
# countries use the same reference.
references = sorted(
    {reference for country in countries for reference in proxies["countries"][country]}
)
reference_ratios = {}
population = pd.read_parquet(snakemake.input.population_summaries)
# Match the population denominator to the EUBUCCO count-statistics coverage,
# rather than using whole-country totals or floor-area reference coverage.
population = population.loc[population.population_kind.eq("count_reference")]
for country in references:
    # Translate workflow country IDs to the codes used in EUBUCCO statistics.
    selected = stats.loc[stats.country.eq(snakemake.params.country_codes[country])]
    inhabitants = population.loc[population.country_id.eq(country), "population"].sum()
    # Shares use all buildings as the denominator, so the two sectors need
    # not sum to one; the commercial numerator also includes public buildings.
    reference_ratios[country] = reference_count_ratios(selected, inhabitants)

# Keep the output schema even when the plan has no countries needing proxies.
columns = [
    "residential_share",
    "commercial_share",
    "residential_per_person",
    "commercial_per_person",
]
# Average ratios across references, not pooled building/population totals;
# larger reference countries therefore do not receive greater weight.
table = pd.DataFrame(
    [
        {
            "country_id": country,
            **pd.DataFrame(
                [reference_ratios[ref] for ref in proxies["countries"][country]]
            )
            .mean()
            .to_dict(),
        }
        for country in countries
    ],
    columns=["country_id", *columns],
)
Path(snakemake.output.table).parent.mkdir(parents=True, exist_ok=True)
table.to_parquet(snakemake.output.table, index=False)
