"""Estimate count proxies using population over the EUBUCCO statistics coverage."""

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import geopandas as gpd
import pandas as pd
from _building_count import reference_count_ratios
from _eubucco import read_plan
from _schemas import validate_building_count_proxies, validate_eubucco_stats

if TYPE_CHECKING:
    snakemake: Any

sys.stderr = open(snakemake.log[0], "w")
plan = read_plan(snakemake.input.plan)
stats = validate_eubucco_stats(snakemake.input.stats)
regions = gpd.read_parquet(snakemake.input.regions).set_index("region_id")
countries = sorted(
    {
        regions.at[region_id, "country_id"]
        for region_id, region in plan["regions"].items()
        if "microsoft" in {region["residential_source"], region["commercial_source"]}
    }
)
proxies = snakemake.params.proxies
references = sorted(
    {reference for country in countries for reference in proxies["countries"][country]}
)
reference_ratios = {}
population = pd.read_parquet(snakemake.input.population_summaries)
population = population.loc[population.population_kind.eq("count_reference")]
for country in references:
    selected = stats.loc[stats.country.eq(snakemake.params.country_codes[country])]
    inhabitants = population.loc[population.country_id.eq(country), "population"].sum()
    reference_ratios[country] = reference_count_ratios(selected, inhabitants)

columns = [
    "residential_share",
    "commercial_share",
    "residential_per_person",
    "commercial_per_person",
]
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
validate_building_count_proxies(table)
Path(snakemake.output.table).parent.mkdir(parents=True, exist_ok=True)
table.to_parquet(snakemake.output.table, index=False)
