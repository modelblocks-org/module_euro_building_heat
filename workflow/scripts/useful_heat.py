"""Calculate annual useful heat demand from prepared final energy demand."""

import sys
from typing import TYPE_CHECKING, Any

import pandas as pd
from final_heat_demand import match_demand_years

if TYPE_CHECKING:
    snakemake: Any


HEAT_END_USES = ("space_heat", "hot_water", "cooking")
EFFICIENCY_PARAMETERS = {
    "biomass_and_waste": "biowaste",
    "gas": "gas",
    "oil": "oil",
    "renewable_heat": "solar_thermal",
    "solar_thermal": "solar_thermal",
    "solid_fossil": "solid_fossil",
    "electricity": "electricity",
}
UNIT_EFFICIENCY_CARRIERS = {"ambient_heat", "direct_electric", "heat", "heat_pump"}


def calculate_useful_heat(
    path_to_final_demand: str,
    paths_to_residential_useful_baselines: list[str],
    paths_to_services_useful_baselines: list[str],
    heat_tech_params: dict[str, dict[str, float]],
    useful_heat_demand_source: str,
    demand_years: list[int],
    path_to_output: str,
) -> None:
    """Convert final demand and optionally prioritize published useful heat."""
    final_demand = _read_final_demand(path_to_final_demand)
    calculated = calculate_useful_heat_from_final(final_demand, heat_tech_params)

    if useful_heat_demand_source == "actual":
        published = _read_published_useful_heat(
            {
                "residential": paths_to_residential_useful_baselines,
                "services": paths_to_services_useful_baselines,
            },
            demand_years,
        )
        calculated = apply_published_precedence(calculated, published)

    _write_useful_heat(calculated, path_to_output)


def _read_final_demand(path: str) -> pd.DataFrame:
    demand = pd.read_parquet(path).squeeze("columns")
    return demand.unstack("year").sort_index()


def calculate_useful_heat_from_final(
    annual_final_demand: pd.DataFrame, heat_tech_params: dict[str, dict[str, float]]
) -> pd.DataFrame:
    """Multiply final demand by carrier efficiencies and aggregate carriers."""
    demands = []
    available_end_uses = annual_final_demand.index.get_level_values("end_use")
    for end_use in HEAT_END_USES:
        if end_use not in available_end_uses:
            continue
        final = annual_final_demand.loc[[end_use]]
        efficiencies = _efficiencies(
            heat_tech_params[end_use],
            final.index.get_level_values("carrier_name").unique(),
        )
        useful = (
            final.mul(efficiencies, level="carrier_name", axis=0)
            .groupby(level=["end_use", "country_code", "cat_name"])
            .sum()
        )
        minimum = (
            final.mul(efficiencies.min())
            .groupby(level=["end_use", "country_code", "cat_name"])
            .sum()
        )
        if not useful.ge(minimum).all().all():
            raise RuntimeError(f"Useful {end_use} demand failed its efficiency check.")
        demands.append(useful)
    if not demands:
        return pd.DataFrame()
    return pd.concat(demands).stack().unstack(["end_use", "cat_name"]).sort_index()


def _efficiencies(params: dict[str, float], carriers: pd.Index) -> pd.Series:
    return pd.Series(
        {
            carrier: (
                1.0
                if carrier in UNIT_EFFICIENCY_CARRIERS
                else params[EFFICIENCY_PARAMETERS[carrier]]
            )
            for carrier in carriers
        }
    )


def _read_published_useful_heat(
    baseline_paths: dict[str, list[str]], demand_years: list[int]
) -> pd.DataFrame:
    """Read standardized published useful heat and match it to demand years."""
    pieces = []
    for sector, paths in baseline_paths.items():
        baseline = pd.concat(
            [pd.read_parquet(path) for path in paths], ignore_index=True
        )
        baseline = baseline.loc[
            baseline["end_use"].isin(HEAT_END_USES)
            & (baseline["country_code"] != "GBR")
        ]
        values = baseline.groupby(["end_use", "country_code", "year"])["value"].sum()
        matched = match_demand_years(values, demand_years, ["end_use", "country_code"])
        pieces.append(
            matched.unstack("end_use")
            .assign(
                cat_name={"residential": "household", "services": "commercial"}[sector]
            )
            .set_index("cat_name", append=True)
        )
    return pd.concat(pieces).unstack("cat_name").sort_index()


def apply_published_precedence(
    calculated: pd.DataFrame, published: pd.DataFrame
) -> pd.DataFrame:
    """Replace calculated values only where published values are available."""
    result = calculated.copy()
    result.update(published)
    return result


def _write_useful_heat(demand: pd.DataFrame, path: str) -> None:
    result = demand.stack(["end_use", "cat_name"], future_stack=True)
    if result.isna().any():
        raise ValueError("Annual useful heat demand contains missing values.")
    result.rename("value").to_frame().to_parquet(path)


if __name__ == "__main__":
    sys.stderr = open(snakemake.log[0], "w", buffering=1)
    calculate_useful_heat(
        path_to_final_demand=snakemake.input.final_demand,
        paths_to_residential_useful_baselines=[
            snakemake.input.residential_useful_baseline
        ],
        paths_to_services_useful_baselines=[snakemake.input.services_useful_baseline],
        heat_tech_params=snakemake.params.heat_tech_params,
        useful_heat_demand_source=snakemake.params.useful_heat_demand,
        demand_years=snakemake.params.demand_years,
        path_to_output=snakemake.output.total_demand,
    )
