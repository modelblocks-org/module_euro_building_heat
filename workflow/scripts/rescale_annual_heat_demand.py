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
from gridded_heat_demand import write_heat_demand_rasters

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


def country_normalised_share(
    weights: pd.Series, shape_to_country: pd.Series, label: str
) -> pd.Series:
    """Normalise one non-negative spatial support over all shapes per country."""
    if not np.isfinite(weights.to_numpy()).all() or (weights < 0).any():
        raise ValueError(f"Invalid {label}: weights must be finite and non-negative.")
    totals = weights.groupby(shape_to_country).sum()
    zero_countries = sorted(totals[totals <= 0].index.tolist())
    if zero_countries:
        raise ValueError(
            f"Cannot distribute demand with zero {label}: {zero_countries}"
        )
    return weights / shape_to_country.map(totals)


def rescale_to_shapes(
    national_demand: pd.DataFrame,
    shape_to_country: pd.Series,
    population: pd.Series,
    space_heat_weights: pd.DataFrame,
    commercial_weights: pd.DataFrame,
    residential_hot_water_weights: pd.Series,
    commercial_hot_water_weights: pd.Series,
) -> pd.DataFrame:
    """Use sector support for heating, adding HDD only for space heat."""
    common_shapes = shape_to_country.index.intersection(population.index).intersection(
        space_heat_weights.index
    )
    if common_shapes.empty:
        raise ValueError("No shapes overlap with calculated population weights.")
    missing_population = sorted(shape_to_country.index.difference(population.index))
    if missing_population:
        raise ValueError(
            f"No population weights found for shape IDs: {missing_population}"
        )
    missing_heat_weights = sorted(
        shape_to_country.index.difference(space_heat_weights.index)
    )
    if missing_heat_weights:
        raise ValueError(
            f"No residential space-heating weights found for shape IDs: {missing_heat_weights}"
        )

    shape_to_country = shape_to_country.loc[common_shapes]
    population = population.loc[common_shapes].fillna(0)
    space_heat_weights = space_heat_weights.loc[common_shapes]
    missing_countries = sorted(set(shape_to_country) - set(national_demand.columns))
    if missing_countries:
        raise ValueError(
            "No national heat demand found for shape country_id values: "
            f"{missing_countries}"
        )

    population_share = country_normalised_share(
        population, shape_to_country, "shape population"
    )
    residential_space_heat_share = pd.DataFrame(
        {
            year: country_normalised_share(
                space_heat_weights[year],
                shape_to_country,
                f"HDD-adjusted residential support ({year})",
            )
            for year in national_demand.index.get_level_values("year").unique()
        }
    )
    commercial_share = pd.DataFrame(
        {
            year: country_normalised_share(
                commercial_weights.loc[common_shapes, year],
                shape_to_country,
                f"HDD-adjusted commercial support ({year})",
            )
            for year in residential_space_heat_share.columns
        }
    )
    commercial_space_heat = (
        national_demand.index.get_level_values("end_use") == "space_heat"
    ) & (national_demand.index.get_level_values("cat_name") == "commercial")
    household_space_heat = (
        national_demand.index.get_level_values("end_use") == "space_heat"
    ) & (national_demand.index.get_level_values("cat_name") == "household")
    hot_water_shares = {
        category: country_normalised_share(
            weights.reindex(common_shapes),
            shape_to_country,
            f"{category} hot water structural support",
        )
        for category, weights in {
            "household": residential_hot_water_weights,
            "commercial": commercial_hot_water_weights,
        }.items()
    }
    columns = {}
    for shape_id, country_id in shape_to_country.items():
        values = national_demand[country_id] * population_share.loc[shape_id]
        years = national_demand.index.get_level_values("year")[household_space_heat]
        values.loc[household_space_heat] = (
            national_demand.loc[household_space_heat, country_id]
            * residential_space_heat_share.loc[shape_id, years].to_numpy()
        )
        years = national_demand.index.get_level_values("year")[commercial_space_heat]
        values.loc[commercial_space_heat] = (
            national_demand.loc[commercial_space_heat, country_id]
            * commercial_share.loc[shape_id, years].to_numpy()
        )
        for category, shares in hot_water_shares.items():
            hot_water = (
                national_demand.index.get_level_values("end_use") == "hot_water"
            ) & (national_demand.index.get_level_values("cat_name") == category)
            values.loc[hot_water] = (
                national_demand.loc[hot_water, country_id] * shares.loc[shape_id]
            )
        columns[shape_id] = values
    demand = pd.DataFrame(columns)
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
        disaggregated_demand.T.groupby(shape_to_country).sum().T
    )
    national_country_totals = national_demand[disaggregated_country_totals.columns]

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
    for country_code in disaggregated_country_totals.columns[~matching.all(axis=0)]:
        logger.warning(
            "  %s: differences in TWh: %s; percent: %s",
            country_code,
            discrepancies[country_code].to_dict(),
            discrepancy_pct[country_code].to_dict(),
        )
    raise ValueError("Shape disaggregation changed national heat-demand totals.")


def country_allocation_factors(
    grid_shapes,
    national_demand,
    shape_to_country,
    annual_weights,
    severity,
    weather_demand_years,
    category="household",
):
    """Return the original country-normalised factors per weather/shape intersection."""
    mask = (national_demand.index.get_level_values("cat_name") == category) & (
        national_demand.index.get_level_values("end_use") == "space_heat"
    )
    national = national_demand.loc[mask].groupby(level="year").sum()
    country_weights = annual_weights.groupby(shape_to_country).sum()
    countries = grid_shapes.id.map(shape_to_country)
    if countries.isna().any():
        raise ValueError("Weather/shape intersections contain unknown shape IDs.")
    factors = []
    pairs = list(weather_demand_years.items())
    for weather_year, demand_year in pairs:
        scale = (
            national.loc[demand_year].reindex(country_weights.index)
            * 1e6
            / country_weights[demand_year]
        )
        factors.append(
            countries.map(scale).to_numpy()
            * severity.sel(
                weather_year=weather_year, site=grid_shapes.site.to_numpy()
            ).values
        )
    factors = np.asarray(factors)
    if not np.isfinite(factors).all():
        raise ValueError("Non-finite country allocation factors for demand raster.")
    return factors


def main() -> None:
    """Main Snakemake process."""
    demand = read_national_demand(snakemake.input.annual_demand)
    mapping = country_map(snakemake.input.shapes)
    population = shape_population(snakemake.input.population)
    weather_demand_years = {
        int(k): int(v) for k, v in snakemake.params.weather_demand_years.items()
    }
    elasticity = float(snakemake.params.hdd_elasticity)
    with (
        xr.open_dataarray(snakemake.input.space_heat_weight) as structural,
        xr.open_dataarray(snakemake.input.commercial_weights) as commercial,
        xr.open_dataset(snakemake.input.annual_weights) as weights,
    ):
        space_heat_weights = weights.residential.transpose("id", "year").to_pandas()
        commercial_weights = weights.commercial.transpose("id", "year").to_pandas()
        severity = weights.severity.load()
        residential_hot_water_weights = structural.sum("site").to_series()
        commercial_hot_water_weights = commercial.sum("site").to_series()
    shapes = gpd.read_parquet(snakemake.input.shapes)

    scaled = rescale_to_shapes(
        demand,
        mapping,
        population,
        space_heat_weights,
        commercial_weights,
        residential_hot_water_weights,
        commercial_hot_water_weights,
    )
    report_country_total_discrepancies(demand, scaled, mapping)
    tidy = tidy_annual_heat_demand(scaled)
    validated = _schemas.AnnualHeatDemandSchema.validate(tidy)
    validated.attrs["units"] = "TWh"
    validated.to_parquet(snakemake.output.annual_demand, index=False)
    grid_shapes = gpd.read_parquet(snakemake.input.grid_shapes)
    factors = {
        category: country_allocation_factors(
            grid_shapes,
            demand,
            mapping,
            sector_weights,
            severity,
            weather_demand_years,
            category,
        )
        for category, sector_weights in [
            ("household", space_heat_weights),
            ("commercial", commercial_weights),
        ]
    }
    write_heat_demand_rasters(
        validated,
        shapes,
        snakemake.input.residential_raster,
        snakemake.input.commercial_raster,
        grid_shapes,
        factors,
        weather_demand_years,
        {
            "space_heat": snakemake.output.space_heat,
            "hot_water": snakemake.output.hot_water,
        },
        hdd_elasticity=elasticity,
        hdd_base_temperature=severity.attrs["base_temperature_celsius"],
    )

    _plots.plot_annual_heat_demand_choropleth(
        shapes, validated, snakemake.output.choropleth
    )
    weather_year, demand_year = next(iter(weather_demand_years.items()))
    _plots.plot_floor_area(
        snakemake.output.space_heat,
        1,
        f"Space-heating demand — {demand_year}\nWeather year: {weather_year}",
        snakemake.output.density,
        snakemake.params.chunk_size,
        snakemake.params.plotting["max_size"],
        shapes,
        snakemake.params.plotting["outline"],
        colorbar_label="Annual space-heating demand (MWh/cell; 1 ha cells)",
    )


if __name__ == "__main__":
    sys.stderr = open(snakemake.log[0], "w", buffering=1)
    logging.basicConfig(level=logging.INFO)
    main()
