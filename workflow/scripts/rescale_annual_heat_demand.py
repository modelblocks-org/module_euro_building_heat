"""Scale national annual heat demand to arbitrary Modelblocks shapes."""

import logging
import sys
from typing import TYPE_CHECKING, Any

import _plots
import _schemas
import geopandas as gpd
import numpy as np
import pandas as pd
import xarray as xr

if TYPE_CHECKING:
    snakemake: Any

logger = logging.getLogger(__name__)


def read_national_demand(path: str) -> pd.DataFrame:
    """Read national annual heat demand from Parquet."""
    return (
        pd.read_parquet(path)
        .squeeze("columns")
        .unstack("country_code")
        .rename(columns=str.upper)
    )


def shape_population(path: str) -> pd.Series:
    """Read total population assigned to each shape."""
    population = xr.open_dataarray(path, decode_timedelta=True).sum("site").to_series()
    if population.index.has_duplicates:
        duplicate_ids = sorted(
            population.index[population.index.duplicated(keep=False)].unique()
        )
        raise ValueError(
            f"Population weights contain duplicate shape IDs: {duplicate_ids}"
        )
    return population.rename("population")


def country_map(path: str) -> pd.Series:
    """Read the mapping from shape IDs to ISO alpha-3 country IDs."""
    shapes = gpd.read_parquet(path)
    required = {"shape_id", "country_id"}
    missing = required.difference(shapes.columns)
    if missing:
        raise ValueError(f"Missing required shape columns: {sorted(missing)}")

    mapping = (
        shapes.set_index("shape_id")["country_id"].astype(str).str.strip().str.upper()
    )
    if mapping.index.has_duplicates:
        duplicate_ids = sorted(
            mapping.index[mapping.index.duplicated(keep=False)].unique()
        )
        raise ValueError(f"Shapes contain duplicate shape IDs: {duplicate_ids}")
    return mapping


def rescale_to_shapes(
    national_demand: pd.DataFrame, shape_to_country: pd.Series, population: pd.Series
) -> pd.DataFrame:
    """Distribute national annual demand to shapes using population shares."""
    common_shapes = shape_to_country.index.intersection(population.index)
    if common_shapes.empty:
        raise ValueError("No shapes overlap with calculated population weights.")
    missing_population = sorted(shape_to_country.index.difference(population.index))
    if missing_population:
        raise ValueError(
            f"No population weights found for shape IDs: {missing_population}"
        )

    shape_to_country = shape_to_country.loc[common_shapes]
    population = population.loc[common_shapes].fillna(0)
    missing_countries = sorted(set(shape_to_country) - set(national_demand.columns))
    if missing_countries:
        raise ValueError(
            "No national heat demand found for shape country_id values: "
            f"{missing_countries}"
        )

    country_population = population.groupby(shape_to_country).sum()
    zero_population_countries = sorted(
        country_population[country_population <= 0].index.tolist()
    )
    if zero_population_countries:
        raise ValueError(
            "Cannot distribute national heat demand for countries with zero "
            f"shape population: {zero_population_countries}"
        )

    population_share = population / shape_to_country.map(country_population)
    demand = pd.DataFrame(
        {
            shape_id: national_demand[country_id] * population_share.loc[shape_id]
            for shape_id, country_id in shape_to_country.items()
        }
    )
    return demand


def tidy_annual_heat_demand(disaggregated_demand: pd.DataFrame) -> pd.DataFrame:
    """Convert shape columns and indexed dimensions to the public tidy format."""
    columns = list(_schemas.AnnualHeatDemandSchema.to_schema().columns)
    tidy = (
        disaggregated_demand.rename_axis(columns="shape_id")
        .stack(future_stack=True)
        .rename("heat_demand_twh")
        .rename_axis(index={"cat_name": "category"})
        .reset_index()
    )
    return tidy.loc[:, columns].sort_values(columns[:-1]).reset_index(drop=True)


def report_country_total_discrepancies(
    national_demand: pd.DataFrame,
    disaggregated_demand: pd.DataFrame,
    shape_to_country: pd.Series,
    rtol: float = 1e-6,
    atol: float = 1e-9,
) -> None:
    """Log countries whose disaggregated total does not match annual demand."""
    shape_to_country = shape_to_country.reindex(disaggregated_demand.columns)
    disaggregated_country_totals = (
        disaggregated_demand.T.groupby(shape_to_country).sum().T.sum(axis=0)
    )
    national_country_totals = national_demand.sum(axis=0).reindex(
        disaggregated_country_totals.index
    )

    discrepancies = disaggregated_country_totals.sub(national_country_totals)
    reference = national_country_totals.where(national_country_totals != 0)
    discrepancy_pct = discrepancies.div(reference).mul(100)
    matching = np.isclose(
        disaggregated_country_totals,
        national_country_totals,
        rtol=rtol,
        atol=atol,
        equal_nan=False,
    )
    if matching.all():
        return

    logger.warning(
        "Annual and disaggregated heat demand totals differ for these countries "
        "(disaggregated - annual):"
    )
    for country_code in disaggregated_country_totals.index[~matching]:
        logger.warning(
            "  %s: %.6g TWh (%.6g%%)",
            country_code,
            discrepancies[country_code],
            discrepancy_pct[country_code],
        )


def main() -> None:
    """Main Snakemake process."""
    demand = read_national_demand(snakemake.input.annual_demand)
    mapping = country_map(snakemake.input.shapes)
    population = shape_population(snakemake.input.population)
    shapes = gpd.read_parquet(snakemake.input.shapes)

    scaled = rescale_to_shapes(demand, mapping, population)
    report_country_total_discrepancies(demand, scaled, mapping)
    tidy = tidy_annual_heat_demand(scaled)
    validated = _schemas.AnnualHeatDemandSchema.validate(tidy)
    validated.attrs["units"] = "TWh"
    validated.to_parquet(snakemake.output.annual_demand, index=False)
    _plots.plot_annual_heat_demand_choropleth(
        shapes, validated, snakemake.output.choropleth
    )


if __name__ == "__main__":
    sys.stderr = open(snakemake.log[0], "w", buffering=1)
    logging.basicConfig(level=logging.INFO)
    main()
