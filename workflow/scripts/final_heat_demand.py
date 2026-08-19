"""Prepare annual final heat demand from standardised baselines."""

from pathlib import Path
from typing import Any

import pandas as pd
import xarray as xr


def prepare_final_heat_demand(
    path_to_energy_balance: str,
    paths_to_residential_baselines: list[str],
    paths_to_services_baselines: list[str],
    paths_to_official_residential_demand: list[str],
    paths_to_official_services_demand: list[str],
    path_to_shapes: str,
    path_to_population: str | None,
    path_to_carrier_names: str,
    data_proxies: dict[str, dict[str, list[str]]],
    country_codes: list[str],
    model_years: list[int],
    path_to_final_demand: str,
) -> None:
    """Match years, scale end-use shares, and proxy missing annual demand."""
    model_years = [int(year) for year in model_years]
    data_proxies = data_proxies or {}
    energy_balances = _read_energy_balances(
        path_to_energy_balance, path_to_carrier_names
    )

    balance_proxies = data_proxies.get("annual_energy_balance", {})
    if set(country_codes) & set(balance_proxies):
        population = _read_country_population(path_to_shapes, path_to_population)
        energy_balances = {
            sector: proxy_energy_balance(
                balance, population, balance_proxies, country_codes
            )
            for sector, balance in energy_balances.items()
        }

    energy_balances = {
        sector: balance.loc[:, model_years]
        for sector, balance in energy_balances.items()
    }
    sector_balances = {
        "residential": energy_balances["residential"],
        "services": energy_balances["services"].add(
            energy_balances["other"], fill_value=0
        ),
    }
    baseline_paths = {
        "residential": paths_to_residential_baselines,
        "services": paths_to_services_baselines,
    }
    official_paths = {
        "residential": paths_to_official_residential_demand,
        "services": paths_to_official_services_demand,
    }
    proxy_names = {"residential": "household_end_use", "services": "jrc_idees"}

    results = []
    for sector in ("residential", "services"):
        baseline = _read_sector_baseline(baseline_paths[sector])
        shares = calculate_end_use_shares(baseline)
        shares = match_model_years(
            shares, model_years, ["carrier_name", "country_code"]
        )
        demand = scale_to_energy_balance(shares, sector_balances[sector])
        demand = _allocate_ambient_heat(demand, sector_balances[sector])
        official = read_official_final_demand(official_paths[sector], model_years)
        demand = overlay_official_final_demand(demand, official)
        demand = _to_sector_wide(demand, sector, model_years)
        demand = proxy_end_use_demand(
            demand,
            sector_balances[sector],
            data_proxies.get(proxy_names[sector], {}),
            country_codes,
            sector,
        )
        results.append(demand)

    _write_final_demand(
        pd.concat(results).sort_index(), path_to_final_demand, country_codes
    )


def _read_sector_baseline(paths: list[str]) -> pd.DataFrame:
    """Read baselines, giving later (country-specific) sources precedence."""
    baseline = pd.DataFrame()
    for path in paths:
        source = pd.read_csv(path)
        if baseline.empty:
            baseline = source
            continue

        source_country_years = pd.MultiIndex.from_frame(
            source[["country_code", "year"]].drop_duplicates()
        )
        baseline_country_years = pd.MultiIndex.from_frame(
            baseline[["country_code", "year"]]
        )
        baseline = baseline.loc[~baseline_country_years.isin(source_country_years)]
        baseline = pd.concat([baseline, source], ignore_index=True)
    return baseline


def calculate_end_use_shares(baseline: pd.DataFrame) -> pd.Series:
    """Calculate observed end-use shares for each carrier total."""
    values = baseline.set_index(["carrier_name", "end_use", "country_code", "year"])[
        "value"
    ].sort_index()
    totals = values.groupby(level=["carrier_name", "country_code", "year"]).transform(
        "sum"
    )
    return values.div(totals).dropna().rename("value")


def match_model_years(
    values: pd.Series, model_years: list[int], group_levels: list[str]
) -> pd.Series:
    """Repeat the nearest observed year; ties use the earlier source year."""
    pieces = []
    index_names = list(values.index.names)
    for _, group in values.groupby(level=group_levels):
        available_years = group.index.get_level_values("year").unique()
        for target_year in model_years:
            source_year = min(
                available_years, key=lambda year: (abs(year - target_year), year)
            )
            selected = group.xs(source_year, level="year").rename("value")
            pieces.append(
                selected.reset_index()
                .assign(year=target_year)
                .set_index(index_names)["value"]
            )
    return pd.concat(pieces).sort_index()


def read_official_final_demand(paths: list[str], model_years: list[int]) -> pd.Series:
    """Read absolute country statistics and match them to the model years."""
    index_names = ["carrier_name", "end_use", "country_code", "year"]
    if not paths:
        empty_index = pd.MultiIndex.from_arrays(
            [[] for _ in index_names], names=index_names
        )
        return pd.Series(dtype=float, index=empty_index, name="value")

    baseline = pd.concat([pd.read_csv(path) for path in paths], ignore_index=True)
    values = baseline.set_index(index_names)["value"].sort_index()
    return match_model_years(
        values,
        model_years,
        ["carrier_name", "end_use", "country_code"],
    )


def overlay_official_final_demand(
    calculated: pd.Series, official: pd.Series
) -> pd.Series:
    """Replace complete calculated country-years with official final demand."""
    if official.empty:
        return calculated

    official_country_years = set(
        zip(
            official.index.get_level_values("country_code"),
            official.index.get_level_values("year"),
        )
    )
    calculated_country_years = zip(
        calculated.index.get_level_values("country_code"),
        calculated.index.get_level_values("year"),
    )
    keep = [
        country_year not in official_country_years
        for country_year in calculated_country_years
    ]
    return pd.concat([calculated.loc[keep], official]).sort_index()


def scale_to_energy_balance(
    end_use_shares: pd.Series, energy_balance: pd.DataFrame
) -> pd.Series:
    """Scale end-use shares by carrier totals from the model-year balance."""
    balance = energy_balance.stack()
    balance.index = balance.index.set_names(["carrier_name", "country_code", "year"])
    lookup = pd.MultiIndex.from_arrays(
        [end_use_shares.index.get_level_values(name) for name in balance.index.names],
        names=balance.index.names,
    )
    totals = pd.Series(balance.reindex(lookup).to_numpy(), index=end_use_shares.index)
    return totals.mul(end_use_shares).dropna().rename("value")


def _allocate_ambient_heat(
    demand: pd.Series, energy_balance: pd.DataFrame
) -> pd.Series:
    """Allocate ambient heat to space heating when the baseline has no split."""
    ambient = energy_balance.loc[
        energy_balance.index.get_level_values("carrier_name") == "ambient_heat"
    ]
    if ambient.empty or "ambient_heat" in demand.index.get_level_values("carrier_name"):
        return demand
    ambient = (
        ambient.assign(end_use="space_heat")
        .set_index("end_use", append=True)
        .stack()
        .reorder_levels(demand.index.names)
        .rename("value")
    )
    return pd.concat([demand, ambient]).sort_index()


def _to_sector_wide(
    demand: pd.Series, sector: str, model_years: list[int]
) -> pd.DataFrame:
    category = {"residential": "household", "services": "commercial"}[sector]
    return (
        demand.where(demand > 0)
        .dropna()
        .unstack("year")
        .reindex(columns=model_years)
        .assign(cat_name=category)
        .set_index("cat_name", append=True)
        .reorder_levels(["end_use", "carrier_name", "country_code", "cat_name"])
        .sort_index()
    )


def proxy_energy_balance(
    energy_balance: pd.DataFrame,
    country_population: pd.Series,
    proxies: dict[str, list[str]],
    country_codes: list[str],
) -> pd.DataFrame:
    """Proxy missing balances from mean reference intensity and population."""
    existing = set(energy_balance.index.get_level_values("country_code"))
    additions = []
    for country in country_codes:
        if country in existing:
            continue
        references = proxies.get(country)
        if not references:
            raise ValueError(
                f"Missing energy balance for {country}, and no proxy is configured."
            )
        missing = sorted(set(references) - existing)
        if missing:
            raise ValueError(
                f"Energy-balance proxy for {country} references missing countries: "
                f"{missing}."
            )
        populations = country_population.loc[[country, *references]]
        if not (populations > 0).all():
            invalid = sorted(populations[populations <= 0].index)
            raise ValueError(f"Proxy population must be positive for: {invalid}.")
        intensities = [
            energy_balance.xs(reference, level="country_code").div(
                populations[reference]
            )
            for reference in references
        ]
        proxied = (
            pd.concat(intensities)
            .groupby(level="carrier_name")
            .mean()
            .mul(populations[country])
            .assign(country_code=country)
            .set_index("country_code", append=True)
            .reorder_levels(energy_balance.index.names)
        )
        additions.append(proxied)
    if additions:
        energy_balance = pd.concat([energy_balance, *additions]).sort_index()
    return energy_balance


def proxy_end_use_demand(
    demand: pd.DataFrame,
    energy_balance: pd.DataFrame,
    proxies: dict[str, list[str]],
    country_codes: list[str],
    sector: str,
) -> pd.DataFrame:
    """Apply mean reference-country end-use shares to missing countries."""
    non_ambient = demand.loc[
        demand.index.get_level_values("carrier_name") != "ambient_heat"
    ]
    existing = set(non_ambient.index.get_level_values("country_code"))
    additions = []
    for country in country_codes:
        if country in existing:
            continue
        references = proxies.get(country)
        if not references:
            raise ValueError(
                f"Missing {sector} end-use data for {country}, and no proxy is configured."
            )
        missing = sorted(set(references) - existing)
        if missing:
            raise ValueError(
                f"{sector.capitalize()} proxy for {country} references countries "
                f"without end-use data: {missing}."
            )
        shares = _mean_reference_end_use_shares(non_ambient, references)
        shares = (
            shares.to_frame("value")
            .assign(country_code=country)
            .set_index("country_code", append=True)["value"]
            .reorder_levels(["carrier_name", "end_use", "country_code", "year"])
        )
        target_balance = energy_balance.xs(
            country, level="country_code", drop_level=False
        )
        proxied = scale_to_energy_balance(shares, target_balance)
        if proxied.empty:
            raise ValueError(
                f"{sector.capitalize()} proxy for {country} produced no demand."
            )
        additions.append(_to_sector_wide(proxied, sector, list(demand.columns)))
    if additions:
        demand = pd.concat([demand, *additions]).sort_index()
    return demand


def _mean_reference_end_use_shares(
    demand: pd.DataFrame, reference_countries: list[str]
) -> pd.Series:
    reference = demand.loc[
        demand.index.get_level_values("country_code").isin(reference_countries)
    ].stack()
    totals = reference.groupby(
        level=["carrier_name", "country_code", "year"]
    ).transform("sum")
    return (
        reference.div(totals)
        .dropna()
        .groupby(level=["carrier_name", "end_use", "year"])
        .mean()
    )


def _read_energy_balances(
    path_to_energy_balance: str, path_to_carrier_names: str
) -> dict[str, pd.DataFrame]:
    energy_balance = pd.read_csv(
        path_to_energy_balance,
        index_col=["cat_code", "carrier_code", "unit", "country", "year"],
    ).squeeze("columns")
    carrier_names = pd.read_csv(path_to_carrier_names, index_col=0)
    balance_codes = {
        "residential": ["FC_OTH_HH_E"],
        "services": ["FC_OTH_CP_E"],
        "other": ["FC_OTH_AF_E", "FC_OTH_FISH_E", "FC_OTH_NSP_E"],
    }
    return {
        sector: _slice_energy_balance_by_sector(
            energy_balance, carrier_names, cat_codes, sector
        )
        for sector, cat_codes in balance_codes.items()
    }


def _slice_energy_balance_by_sector(
    df: pd.Series, carrier_names: pd.DataFrame, cat_codes: list[str], sector: str
) -> pd.DataFrame:
    carrier_column = {
        "residential": "residential_carrier_name",
        "services": "services_carrier_name",
        "other": "other_carrier_name",
    }[sector]
    carrier_mapping = carrier_names[carrier_column].dropna()
    relevant_carriers = carrier_mapping.drop_duplicates()
    countries = df.index.get_level_values("country").unique()
    years = df.index.get_level_values("year").unique().sort_values()
    relevant_index = pd.MultiIndex.from_product(
        [relevant_carriers, countries], names=["carrier_name", "country_code"]
    )
    available_codes = [
        code for code in cat_codes if code in df.index.get_level_values("cat_code")
    ]
    if not available_codes:
        return pd.DataFrame(0.0, index=relevant_index, columns=years)
    selected = df.loc[available_codes].xs("twh", level="unit")
    return (
        selected.unstack("year")
        .rename(index=carrier_mapping.to_dict(), level="carrier_code")
        .groupby(level=["carrier_code", "country"])
        .sum()
        .reindex(relevant_index, fill_value=0)
        .reindex(columns=years, fill_value=0)
        .rename_axis(index=["carrier_name", "country_code"], columns="year")
    )


def _read_country_population(path_to_shapes: str, path_to_population: str) -> pd.Series:
    shapes = pd.read_parquet(path_to_shapes)
    shape_to_country = shapes.set_index("shape_id")["country_id"]
    # Shape parquet IDs use hyphens while xarray site coordinates use dots.
    shape_to_country.index = shape_to_country.index.str.replace(".", "-", regex=False)
    population = (
        xr.open_dataarray(path_to_population, decode_timedelta=True)
        .sum("site")
        .to_series()
    )
    population.index = population.index.str.replace(".", "-", regex=False)
    return population.groupby(shape_to_country).sum().rename("population")


def _write_final_demand(
    demand: pd.DataFrame, path: str, country_codes: list[str]
) -> None:
    result = demand.stack(list(range(demand.columns.nlevels)), future_stack=True)
    result = result.loc[
        result.index.get_level_values("country_code").isin(country_codes)
    ]
    if result.isna().any():
        raise ValueError("Annual final demand contains missing values.")
    result.rename("value").to_csv(path)


def _as_list(value: Any) -> list[str]:
    if isinstance(value, (str, Path)):
        return [str(value)]
    return [str(path) for path in value]


def _as_optional_path(value: Any) -> str | None:
    paths = _as_list(value)
    return paths[0] if paths else None


if __name__ == "__main__":
    prepare_final_heat_demand(
        path_to_energy_balance=snakemake.input.energy_balance,
        paths_to_residential_baselines=_as_list(snakemake.input.residential_baselines),
        paths_to_services_baselines=_as_list(snakemake.input.services_baselines),
        paths_to_official_residential_demand=_as_list(
            snakemake.input.official_residential_demand
        ),
        paths_to_official_services_demand=_as_list(
            snakemake.input.official_services_demand
        ),
        path_to_shapes=snakemake.input.shapes,
        path_to_population=_as_optional_path(snakemake.input.population),
        path_to_carrier_names=snakemake.input.carrier_names,
        country_codes=snakemake.params.countries,
        model_years=snakemake.params.model_years,
        data_proxies=snakemake.params.data_proxies,
        path_to_final_demand=snakemake.output.final_demand,
    )
