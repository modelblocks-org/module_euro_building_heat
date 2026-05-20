"""Scale national annual heat demand to arbitrary Modelblocks shapes."""

import geopandas as gpd
import pandas as pd
import xarray as xr


def read_national_demand(path: str) -> pd.DataFrame:
    """Read national annual heat demand from the legacy CSV structure."""
    return (
        pd.read_csv(path, index_col=[0, 1, 2, 3])
        .squeeze("columns")
        .unstack("country_code")
    )


def shape_population(path: str) -> pd.Series:
    """Read total population assigned to each shape."""
    population = xr.open_dataarray(path).sum("site").to_series()
    population.index = population.index.astype(str)
    return population.rename("population")


def country_map(path: str) -> pd.Series:
    """Read the mapping from shape IDs to ISO alpha-3 country IDs."""
    shapes = gpd.read_parquet(path)
    required = {"shape_id", "country_id"}
    missing = required.difference(shapes.columns)
    if missing:
        raise ValueError(f"Missing required shape columns: {sorted(missing)}")

    mapping = shapes.set_index("shape_id")["country_id"]
    mapping.index = mapping.index.astype(str).str.replace(".", "-", regex=False)
    return mapping


def rescale_to_shapes(
    national_demand: pd.DataFrame,
    shape_to_country: pd.Series,
    population: pd.Series,
) -> pd.DataFrame:
    """Distribute national annual demand to shapes using population shares."""
    common_shapes = shape_to_country.index.intersection(population.index)
    if common_shapes.empty:
        raise ValueError("No shapes overlap with calculated population weights.")

    shape_to_country = shape_to_country.loc[common_shapes]
    population = population.loc[common_shapes].fillna(0)
    population_share = population.groupby(shape_to_country).transform(
        lambda values: values / values.sum()
    )

    demand = pd.DataFrame(
        {
            shape_id: national_demand[country_id] * population_share.loc[shape_id]
            for shape_id, country_id in shape_to_country.items()
            if country_id in national_demand.columns
        }
    )
    missing_countries = sorted(set(shape_to_country) - set(national_demand.columns))
    if missing_countries:
        raise ValueError(
            "No national heat demand found for shape country_id values: "
            f"{missing_countries}"
        )
    return demand


if __name__ == "__main__":
    demand = read_national_demand(snakemake.input.annual_demand)
    mapping = country_map(snakemake.input.shapes)
    population = shape_population(snakemake.input.population)

    scaled = rescale_to_shapes(demand, mapping, population)
    scaled.to_parquet(snakemake.output[0])
