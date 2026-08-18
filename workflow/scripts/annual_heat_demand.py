"""Calculate annual useful heat demand from standardised baselines."""

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import xarray as xr

TJ_TO_TWH = 1 / 3600
BASELINE_TO_TWH = {"twh": 1.0, "ktoe": 0.01163}
ELECTRIC_HEATING_CARRIERS = {"direct_electric", "heat_pump"}


def get_heat_demand(
    path_to_energy_balance: str,
    paths_to_residential_baselines: list[str],
    paths_to_services_baselines: list[str],
    paths_to_residential_useful_baselines: list[str],
    paths_to_services_useful_baselines: list[str],
    paths_to_official_residential_baselines: list[str],
    paths_to_official_services_baselines: list[str],
    path_to_shapes: str,
    path_to_population: str | None,
    path_to_carrier_names: str,
    heat_tech_params: dict[str, dict[str, float]],
    useful_heat_demand_source: str,
    data_proxies: dict[str, dict[str, list[str]]],
    country_codes: list[str],
    model_years: list[int],
    path_to_electricity_demand: str,
    path_to_output: str,
) -> None:
    """Calculate national annual heat demand in TWh."""
    model_years = [int(year) for year in model_years]
    data_proxies = data_proxies or {}

    energy_balances = _get_energy_balances(
        path_to_energy_balance, path_to_carrier_names
    )
    balance_proxies = data_proxies.get("annual_energy_balance", {})
    if set(country_codes) & set(balance_proxies):
        country_population = _shape_country_population(
            path_to_shapes, path_to_population
        )
        energy_balances = {
            sector: _add_energy_balance_proxies(
                balance, country_population, balance_proxies, country_codes
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
        "residential": paths_to_official_residential_baselines,
        "services": paths_to_official_services_baselines,
    }
    proxy_names = {"residential": "household_end_use", "services": "jrc_idees"}

    final_demand = []
    for sector in ["residential", "services"]:
        demand = _calculate_sector_final_demand(
            sector,
            sector_balances[sector],
            baseline_paths[sector],
            official_paths[sector],
            model_years,
        )
        demand = _add_end_use_proxies(
            demand,
            sector_balances[sector],
            data_proxies.get(proxy_names[sector], {}),
            country_codes,
            sector,
        )
        final_demand.append(demand)

    annual_final_demand = pd.concat(final_demand).sort_index()
    electricity_demand = get_annual_electricity_demand(
        annual_final_demand, energy_balances
    )
    useful_heat_demand = get_national_useful_heat_demand(
        annual_final_demand, energy_balances, heat_tech_params
    )
    if useful_heat_demand_source == "actual":
        published_useful_heat = _get_published_useful_heat_demand(
            {
                "residential": paths_to_residential_useful_baselines,
                "services": paths_to_services_useful_baselines,
            },
            model_years,
        )
        useful_heat_demand.update(published_useful_heat)
    elif useful_heat_demand_source != "calculate_all":
        raise ValueError(
            "heat.useful_heat_demand must be either 'actual' or 'calculate_all', "
            f"not {useful_heat_demand_source!r}."
        )

    for demand, path in [
        (electricity_demand, path_to_electricity_demand),
        (useful_heat_demand, path_to_output),
    ]:
        result = demand.stack([0, 1], future_stack=True).squeeze()
        result = result.loc[
            result.index.get_level_values("country_code").isin(country_codes)
        ]
        result.rename("value").pipe(_check_no_remaining_missing_values).to_csv(path)


def _calculate_sector_final_demand(
    sector: str,
    energy_balance: pd.DataFrame,
    baseline_paths: list[str],
    official_paths: list[str],
    model_years: list[int],
) -> pd.DataFrame:
    """Scale baseline end-use shares and overlay official final-demand data."""
    baseline = pd.concat(
        [pd.read_csv(path) for path in baseline_paths], ignore_index=True
    )
    baseline["value"] *= baseline["unit"].map(BASELINE_TO_TWH)
    if sector == "residential":
        # The residential energy balance identifies this carrier more narrowly.
        baseline["carrier_name"] = baseline["carrier_name"].replace(
            {"renewable_heat": "solar_thermal"}
        )
    shares = _baseline_end_use_shares(baseline, energy_balance.columns)
    mapped = _map_end_use_shares_to_balance(energy_balance, shares)

    ambient_heat = (
        energy_balance.loc[
            energy_balance.index.get_level_values("carrier_name") == "ambient_heat"
        ]
        .assign(end_use="space_heat")
        .set_index("end_use", append=True)
        .stack()
        .reorder_levels(mapped.index.names)
    )
    mapped = pd.concat([mapped, ambient_heat]).groupby(level=mapped.index.names).sum()

    if official_paths:
        official = pd.concat(
            [pd.read_csv(path) for path in official_paths], ignore_index=True
        )
        official["value"] *= official["unit"].map(BASELINE_TO_TWH)
        official = official.loc[official["year"].isin(model_years)]
        official = official.set_index(
            ["carrier_name", "end_use", "country_code", "year"]
        )["value"].sort_index()
        mapped = _overlay_official_demand(mapped, official)

    return (
        mapped.where(mapped > 0)
        .dropna()
        .unstack("year")
        .reindex(columns=model_years)
        .assign(cat_name={"residential": "household", "services": "commercial"}[sector])
        .set_index("cat_name", append=True)
        .reorder_levels(["end_use", "carrier_name", "country_code", "cat_name"])
        .sort_index()
    )


def _baseline_end_use_shares(
    baseline: pd.DataFrame, target_years: pd.Index | list[int]
) -> pd.Series:
    values = baseline.set_index(["carrier_name", "end_use", "country_code", "year"])[
        "value"
    ].sort_index()
    denominator = values.groupby(
        level=["carrier_name", "country_code", "year"]
    ).transform("sum")
    shares = values.div(denominator).replace([np.inf, -np.inf], np.nan).dropna()

    # Baseline releases commonly lag the energy balance by a year or two. Use the
    # closest observed end-use split while retaining the target balance totals.
    target_years = pd.Index([int(year) for year in target_years], name="year")
    pieces = []
    index_names = ["carrier_name", "end_use", "country_code", "year"]
    for _, group in shares.groupby(level=["carrier_name", "country_code"]):
        available_years = group.index.get_level_values("year").unique()
        for target_year in target_years:
            source_year = min(available_years, key=lambda year: abs(year - target_year))
            selected = group.xs(source_year, level="year").rename("value")
            selected = (
                selected.reset_index()
                .assign(year=target_year)
                .set_index(index_names)["value"]
            )
            pieces.append(selected)
    return pd.concat(pieces).sort_index()


def _map_end_use_shares_to_balance(
    energy_balance: pd.DataFrame, end_use_shares: pd.Series
) -> pd.Series:
    balance = energy_balance.stack()
    balance.index = balance.index.set_names(["carrier_name", "country_code", "year"])
    lookup = pd.MultiIndex.from_arrays(
        [
            end_use_shares.index.get_level_values("carrier_name"),
            end_use_shares.index.get_level_values("country_code"),
            end_use_shares.index.get_level_values("year"),
        ],
        names=balance.index.names,
    )
    values = pd.Series(balance.reindex(lookup).to_numpy(), index=end_use_shares.index)
    return values.mul(end_use_shares).dropna().rename("value")


def _overlay_official_demand(mapped: pd.Series, official: pd.Series) -> pd.Series:
    """Replace complete country-years with official baseline values."""
    if official.empty:
        return mapped
    official_country_years = set(
        zip(
            official.index.get_level_values("country_code"),
            official.index.get_level_values("year"),
        )
    )
    mapped_country_years = zip(
        mapped.index.get_level_values("country_code"),
        mapped.index.get_level_values("year"),
    )
    keep = [
        country_year not in official_country_years
        for country_year in mapped_country_years
    ]
    return pd.concat([mapped.loc[keep], official]).sort_index()


def _get_published_useful_heat_demand(
    baseline_paths: dict[str, list[str]], model_years: list[int]
) -> pd.DataFrame:
    """Read published useful heat, selecting the closest available JRC year."""
    pieces = []
    for sector, paths in baseline_paths.items():
        if not paths:
            continue
        baseline = pd.concat([pd.read_csv(path) for path in paths], ignore_index=True)
        baseline["value"] *= baseline["unit"].map(BASELINE_TO_TWH)
        baseline = baseline.loc[
            baseline["end_use"].isin(["space_heat", "hot_water", "cooking"])
        ]
        values = baseline.groupby(["end_use", "country_code", "year"], sort=True)[
            "value"
        ].sum()

        selected_years = []
        for _, group in values.groupby(level=["end_use", "country_code"]):
            available_years = group.index.get_level_values("year").unique()
            for target_year in model_years:
                source_year = min(
                    available_years, key=lambda year: abs(year - target_year)
                )
                selected = (
                    group.xs(source_year, level="year")
                    .rename("value")
                    .reset_index()
                    .assign(year=target_year)
                    .set_index(["end_use", "country_code", "year"])["value"]
                )
                selected_years.append(selected)

        if selected_years:
            pieces.append(
                pd.concat(selected_years)
                .unstack("end_use")
                .assign(
                    cat_name={"residential": "household", "services": "commercial"}[
                        sector
                    ]
                )
                .set_index("cat_name", append=True)
            )

    if not pieces:
        return pd.DataFrame()
    return pd.concat(pieces).unstack("cat_name").sort_index()


def _get_energy_balances(
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
        "residential": "hh_carrier_name",
        "services": "com_carrier_name",
        "other": "oth_carrier_name",
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
    selected = df.loc[available_codes]
    units = selected.index.get_level_values("unit").unique().tolist()
    if units != ["TJ"]:
        raise ValueError(f"Expected only TJ in {sector} energy balance, found {units}.")
    return (
        selected.xs("TJ", level="unit")
        .mul(TJ_TO_TWH)
        .unstack("year")
        .rename(index=carrier_mapping.to_dict(), level="carrier_code")
        .groupby(level=["carrier_code", "country"])
        .sum()
        .reindex(relevant_index, fill_value=0)
        .reindex(columns=years, fill_value=0)
        .rename_axis(index=["carrier_name", "country_code"], columns="year")
    )


def _normalise_shape_ids(values: pd.Index | pd.Series) -> pd.Index:
    return pd.Index(values.astype(str).str.replace(".", "-", regex=False))


def _shape_country_population(
    path_to_shapes: str, path_to_population: str
) -> pd.Series:
    shapes = pd.read_parquet(path_to_shapes)
    required = {"shape_id", "country_id"}
    missing = required.difference(shapes.columns)
    if missing:
        raise ValueError(f"Missing required shape columns: {sorted(missing)}")
    shape_to_country = (
        shapes.set_index("shape_id")["country_id"].astype(str).str.strip().str.upper()
    )
    shape_to_country.index = _normalise_shape_ids(shape_to_country.index)
    population = (
        xr.open_dataarray(path_to_population, decode_timedelta=True)
        .sum("site")
        .to_series()
    )
    population.index = _normalise_shape_ids(population.index)
    return population.groupby(shape_to_country).sum().rename("population")


def _normalise_proxy_map(proxy_map: dict[str, list[str]]) -> dict[str, list[str]]:
    return {
        str(country).strip().upper(): [
            str(reference).strip().upper() for reference in references
        ]
        for country, references in proxy_map.items()
    }


def _add_energy_balance_proxies(
    energy_balance: pd.DataFrame,
    country_population: pd.Series,
    proxies: dict[str, list[str]],
    country_codes: list[str],
) -> pd.DataFrame:
    proxies = _normalise_proxy_map(proxies)
    existing = set(energy_balance.index.get_level_values("country_code"))
    additions = []
    for country in country_codes:
        if country in existing:
            continue
        references = proxies[country]
        populations = country_population.loc[[country, *references]]
        if not (populations > 0).all():
            missing = sorted(populations[populations <= 0].index)
            raise ValueError(f"Missing proxy population: {missing}.")
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


def _add_end_use_proxies(
    demand: pd.DataFrame,
    energy_balance: pd.DataFrame,
    proxies: dict[str, list[str]],
    country_codes: list[str],
    sector: str,
) -> pd.DataFrame:
    proxies = _normalise_proxy_map(proxies)
    existing = set(demand.index.get_level_values("country_code"))
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
        shares = _proxy_end_use_shares(demand, references)
        shares = (
            shares.to_frame("value")
            .assign(country_code=country)
            .set_index("country_code", append=True)["value"]
            .reorder_levels(["carrier_name", "end_use", "country_code", "year"])
        )
        target_balance = energy_balance.xs(
            country, level="country_code", drop_level=False
        )
        proxied = _map_end_use_shares_to_balance(target_balance, shares)
        if proxied.empty:
            raise ValueError(
                f"{sector.capitalize()} proxy for {country} produced no demand."
            )
        additions.append(
            proxied.unstack("year")
            .reindex(columns=demand.columns)
            .assign(
                cat_name={"residential": "household", "services": "commercial"}[sector]
            )
            .set_index("cat_name", append=True)
            .reorder_levels(demand.index.names)
        )
    if additions:
        demand = pd.concat([demand, *additions]).sort_index()
    return demand


def _proxy_end_use_shares(
    demand: pd.DataFrame, reference_countries: list[str]
) -> pd.Series:
    reference = demand.loc[
        demand.index.get_level_values("country_code").isin(reference_countries)
    ].stack()
    frame = reference.rename("value").reset_index()
    frame["carrier_name"] = frame["carrier_name"].replace(
        {carrier: "electricity" for carrier in ELECTRIC_HEATING_CARRIERS}
    )
    reference = frame.groupby(["carrier_name", "end_use", "country_code", "year"])[
        "value"
    ].sum()
    denominator = reference.groupby(
        level=["carrier_name", "country_code", "year"]
    ).transform("sum")
    return (
        reference.div(denominator)
        .dropna()
        .groupby(level=["carrier_name", "end_use", "year"])
        .mean()
    )


def _fill_data_gaps(
    end_use: pd.DataFrame, energy_balances: dict[str, pd.DataFrame], fill: str
) -> pd.DataFrame:
    end_use = end_use.where(end_use > 0)
    household_balance = (
        energy_balances["residential"]
        .groupby(level="country_code")
        .sum()
        .stack()
        .where(lambda values: values > 0)
    )
    if (
        isinstance(end_use.columns, pd.MultiIndex)
        and "cat_name" in end_use.columns.names
        and "household" in end_use.columns.get_level_values("cat_name")
    ):
        columns = end_use.columns.get_level_values("cat_name") == "household"
        household = end_use.loc[:, columns]
        end_use.loc[:, columns] = household.fillna(
            household.div(household_balance, axis=0)
            .groupby(level="country_code")
            .mean()
            .mul(household_balance, level="country_code", axis=0)
        )
    return end_use.where(end_use > 0).fillna(end_use.groupby("country_code").agg(fill))


def get_annual_electricity_demand(
    annual_final_demand: pd.DataFrame, energy_balances: dict[str, pd.DataFrame]
) -> pd.DataFrame:
    """Get heat-related annual electricity demand."""
    demand = annual_final_demand.drop("end_use_electricity", errors="ignore")
    demand = demand.loc[
        demand.index.get_level_values("carrier_name").isin(
            {"electricity", *ELECTRIC_HEATING_CARRIERS}
        )
    ]
    demand = (
        demand.groupby(level=["end_use", "country_code", "cat_name"])
        .sum()
        .stack()
        .unstack(["end_use", "cat_name"])
    )
    return _fill_data_gaps(demand, energy_balances, fill="first")


def get_national_useful_heat_demand(
    annual_final_demand: pd.DataFrame,
    energy_balances: dict[str, pd.DataFrame],
    heat_tech_params: dict[str, dict[str, float]],
) -> pd.DataFrame:
    """Derive useful heat demand from final energy demand."""
    demands = []
    available_end_uses = annual_final_demand.index.get_level_values("end_use")
    for end_use in ["space_heat", "hot_water", "cooking"]:
        if end_use not in available_end_uses:
            continue
        final = annual_final_demand.loc[[end_use]]
        efficiencies = _efficiencies(heat_tech_params[end_use])
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
    demand = pd.concat(demands).stack().unstack(["end_use", "cat_name"])
    return _fill_data_gaps(demand, energy_balances, fill="mean")


def _efficiencies(params: dict[str, float]) -> pd.Series:
    return pd.Series(
        {
            "biogas": params.get("gas-eff", np.nan),
            "biofuel": params.get("biofuel-eff", np.nan),
            "solid_fossil": params.get("solid-fossil-eff", np.nan),
            "natural_gas": params.get("gas-eff", np.nan),
            "manufactured_gas": params.get("gas-eff", np.nan),
            "gas": params.get("gas-eff", np.nan),
            "oil": params.get("oil-eff", np.nan),
            "solar_thermal": params.get("solar-thermal-eff", np.nan),
            "renewable_heat": params.get("solar-thermal-eff", np.nan),
            "electricity": params.get("electricity-eff", np.nan),
            "direct_electric": 1,
            "heat": 1,
            "heat_pump": 1,
            "ambient_heat": 1,
        }
    )


def _check_no_remaining_missing_values(
    df: pd.Series | pd.DataFrame,
) -> pd.Series | pd.DataFrame:
    missing = df.isna()
    has_missing = (
        missing.any().any() if isinstance(missing, pd.DataFrame) else missing.any()
    )
    if not has_missing:
        return df
    if isinstance(df, pd.Series):
        sample = df[missing].head(10).index.to_frame(index=False).to_string(index=False)
    else:
        row_positions, column_positions = np.where(missing.to_numpy())
        sample = pd.DataFrame(
            [
                {"index": df.index[row], "column": df.columns[column]}
                for row, column in zip(row_positions[:10], column_positions[:10])
            ]
        ).to_string(index=False)
    raise ValueError(
        "Annual demand still contains missing values after preprocessing. "
        f"Missing entries include:\n{sample}"
    )


def _as_list(value: Any) -> list[str]:
    if isinstance(value, (str, Path)):
        return [str(value)]
    return [str(path) for path in value]


def _as_optional_path(value: Any) -> str | None:
    paths = _as_list(value)
    return paths[0] if paths else None


if __name__ == "__main__":
    get_heat_demand(
        path_to_energy_balance=snakemake.input.energy_balance,
        paths_to_residential_baselines=_as_list(snakemake.input.residential_baseline),
        paths_to_services_baselines=_as_list(snakemake.input.services_baseline),
        paths_to_residential_useful_baselines=_as_list(
            snakemake.input.residential_useful_baseline
        ),
        paths_to_services_useful_baselines=_as_list(
            snakemake.input.services_useful_baseline
        ),
        paths_to_official_residential_baselines=_as_list(
            snakemake.input.official_residential_baselines
        ),
        paths_to_official_services_baselines=_as_list(
            snakemake.input.official_services_baselines
        ),
        path_to_shapes=snakemake.input.shapes,
        path_to_population=_as_optional_path(snakemake.input.population),
        path_to_carrier_names=snakemake.input.carrier_names,
        heat_tech_params=snakemake.params.heat_tech_params,
        useful_heat_demand_source=snakemake.params.useful_heat_demand,
        country_codes=snakemake.params.countries,
        model_years=snakemake.params.model_years,
        data_proxies=snakemake.params.data_proxies,
        path_to_electricity_demand=snakemake.output.electricity,
        path_to_output=snakemake.output.total_demand,
    )
