"""Scale national annual heat demand to arbitrary Modelblocks shapes."""

import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import _plots
import _schemas
import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import xarray as xr
from aggregate_residential_space_heat_weight import iter_weight_blocks

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


def climate_adjusted_weights(
    structural: xr.DataArray,
    hdd: xr.DataArray,
    weather_demand_years: dict[int, int],
    elasticity: float,
) -> tuple[pd.DataFrame, xr.DataArray]:
    """Sum structural support times HDD^elasticity to shapes for each year.

    Any country-wide HDD reference would cancel during country normalisation.
    Elasticity zero explicitly disables severity, including sites with zero HDD.
    """
    if set(structural.dims) != {"site", "id"}:
        raise ValueError("Structural weights must have dimensions site and id.")
    if (
        structural.id.to_index().has_duplicates
        or structural.site.to_index().has_duplicates
    ):
        raise ValueError("Structural weights contain duplicate shape or site IDs.")
    if not np.isfinite(structural.values).all() or (structural.values < 0).any():
        raise ValueError("Structural weights must be finite and non-negative.")
    if not np.isfinite(elasticity) or elasticity < 0:
        raise ValueError("HDD elasticity must be finite and non-negative.")
    hdd = hdd.sel(site=structural.site, weather_year=list(weather_demand_years))
    if not np.isfinite(hdd.values).all() or (hdd.values < 0).any():
        raise ValueError(
            "HDD must be finite and non-negative for every weather cell/year."
        )
    severity = xr.ones_like(hdd) if elasticity == 0 else hdd**elasticity
    weights = (
        (structural * severity).sum("site").transpose("id", "weather_year").to_pandas()
    )
    weights.columns = [weather_demand_years[int(year)] for year in weights.columns]
    return weights, severity


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
) -> pd.DataFrame:
    """Use heat support for household space heat and population otherwise."""
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
    household_space_heat = (
        national_demand.index.get_level_values("end_use") == "space_heat"
    ) & (national_demand.index.get_level_values("cat_name") == "household")
    columns = {}
    for shape_id, country_id in shape_to_country.items():
        values = national_demand[country_id] * population_share.loc[shape_id]
        years = national_demand.index.get_level_values("year")[household_space_heat]
        values.loc[household_space_heat] = (
            national_demand.loc[household_space_heat, country_id]
            * residential_space_heat_share.loc[shape_id, years].to_numpy()
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


def write_demand_raster(
    source_path: str,
    output_path: str,
    grid_shapes: gpd.GeoDataFrame,
    national_demand: pd.DataFrame,
    shape_to_country: pd.Series,
    annual_weights: pd.DataFrame,
    severity: xr.DataArray,
    weather_demand_years: dict[int, int],
    elasticity: float,
) -> None:
    """Write annual household space heat in MWh per original 100 m cell.

    The same Gregor pixel-centre assignment and country denominators as the
    shape allocation ensure consistent totals.
    """
    mask = (national_demand.index.get_level_values("cat_name") == "household") & (
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
    with rasterio.open(source_path) as source:
        profile = source.profile.copy()
    profile.update(
        driver="GTiff",
        count=len(pairs),
        dtype="float64",
        nodata=0,
        tiled=True,
        blockxsize=512,
        blockysize=512,
        compress="deflate",
        predictor=3,
        BIGTIFF="IF_SAFER",
    )
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    assigned = np.zeros_like(factors)
    raster_totals = np.zeros(len(pairs))
    with rasterio.open(output_path, "w", **profile) as output:
        output.update_tags(
            end_use="household space heating",
            units="MWh/cell",
            allocation="structural support * HDD^elasticity; country-normalised",
            hdd_elasticity=elasticity,
            hdd_base_temperature_celsius=severity.attrs.get(
                "base_temperature_celsius", ""
            ),
        )
        for band, (weather_year, demand_year) in enumerate(pairs, start=1):
            output.set_band_description(
                band, f"household_space_heat_{demand_year}_weather_{weather_year}"
            )
            output.set_band_unit(band, "MWh/cell")
            output.update_tags(band, demand_year=demand_year, weather_year=weather_year)
        for window, block_shape, contributions in iter_weight_blocks(
            source_path, grid_shapes
        ):
            block = np.zeros((len(pairs), *block_shape), dtype=np.float64)
            for index, rows, columns, support in contributions:
                for band in range(len(pairs)):
                    energy = support * factors[band, index]
                    block[band, rows, columns] += energy
                    assigned[band, index] += energy.sum()
            raster_totals += block.sum(axis=(1, 2))
            output.write(block, window=window)
    for band, (_, demand_year) in enumerate(pairs):
        actual = (
            pd.Series(assigned[band]).groupby(countries.reset_index(drop=True)).sum()
        )
        expected = national.loc[demand_year, actual.index] * 1e6
        if not np.allclose(actual, expected, rtol=1e-6, atol=1e-3):
            raise ValueError(f"Demand raster changed country totals for {demand_year}.")
        if not np.isclose(raster_totals[band], expected.sum(), rtol=1e-6, atol=1e-3):
            raise ValueError(
                f"Demand raster cell sum differs from allocated energy for {demand_year}."
            )


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
        xr.open_dataset(snakemake.input.hdd) as weather,
    ):
        space_heat_weights, severity = climate_adjusted_weights(
            structural.load(), weather.hdd.load(), weather_demand_years, elasticity
        )
        severity.attrs.update(weather.hdd.attrs)
    shapes = gpd.read_parquet(snakemake.input.shapes)

    scaled = rescale_to_shapes(demand, mapping, population, space_heat_weights)
    report_country_total_discrepancies(demand, scaled, mapping)
    tidy = tidy_annual_heat_demand(scaled)
    validated = _schemas.AnnualHeatDemandSchema.validate(tidy)
    validated.attrs["units"] = "TWh"
    validated.to_parquet(snakemake.output.annual_demand, index=False)
    _plots.plot_annual_heat_demand_choropleth(
        shapes, validated, snakemake.output.choropleth
    )
    write_demand_raster(
        snakemake.input.residential_raster,
        snakemake.output.raster,
        gpd.read_parquet(snakemake.input.grid_shapes),
        demand,
        mapping,
        space_heat_weights,
        severity,
        weather_demand_years,
        elasticity,
    )


if __name__ == "__main__":
    sys.stderr = open(snakemake.log[0], "w", buffering=1)
    logging.basicConfig(level=logging.INFO)
    main()
