"""Calculate annual useful heat demand from energy statistics."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

sys.path.insert(0, str(Path(__file__).parent))
from ecuk_end_use import (
    read_ecuk_domestic_final_demand,
    read_ecuk_sector_energy_balance,
    read_ecuk_service_end_use_shares,
)
from jrc_idees_heat import read_jrc_heat_tertiary_sector_data

END_USE_CAT_NAMES = {
    "FC_OTH_HH_E_CK": "cooking",
    "FC_OTH_HH_E_SH": "space_heat",
    "FC_OTH_HH_E_WH": "hot_water",
}

CH_ENERGY_CARRIER_TRANSLATION = {
    "Heizöl": "oil",
    "Erdgas": "gas",
    "El. Widerstandsheizungen": "direct_electric",
    "El. Wärmepumpen 1)": "heat_pump",
    "El. Ohm'sche Anlagen": "direct_electric",
    "El. Wärmepumpen": "heat_pump",
    "Elektrizität": "electricity",
    "Holz": "biofuel",
    "Kohle": "solid_fossil",
    "Fernwärme": "heat",
    "Umweltwärme": "ambient_heat",
    "Solar": "solar_thermal",
}

CH_HH_END_USE_TRANSLATION = {
    "Raumwärme": "space_heat",
    "Warmwasser": "hot_water",
    "Prozesswärme": "process_heat",
    "Beleuchtung": "end_use_electricity",
    "Klima, Lüftung, HT": "end_use_electricity",
    "I&K, Unterhaltung": "end_use_electricity",
    "Antriebe, Prozesse": "end_use_electricity",
    "sonstige": "end_use_electricity",
}

idx = pd.IndexSlice
EUROSTAT_TO_ALPHA3 = {"EL": "GRC", "UK": "GBR"}
KTOE_TO_TWH = 0.01163
PJ_TO_TWH = 1 / 3.6
TJ_TO_TWH = 1 / 3600
JRC_HEAT_INDEX_NAMES = [
    "carrier_name",
    "end_use",
    "country_code",
    "unit",
    "energy",
    "year",
]
JRC_FINAL_ENERGY_INDEX_NAMES = [
    "carrier_name",
    "end_use",
    "country_code",
    "unit",
    "year",
]
END_USE_SHARE_INDEX_NAMES = ["carrier_name", "end_use", "country_code", "year"]
HOUSEHOLD_ELECTRIC_PROXY_CARRIERS = ["electricity", "direct_electric", "heat_pump"]


def eurostat_to_alpha3(country_code: str) -> str:
    """Convert Eurostat/JRC alpha-2-like country codes to ISO alpha-3."""
    if country_code in EUROSTAT_TO_ALPHA3:
        return EUROSTAT_TO_ALPHA3[country_code]

    import pycountry

    return pycountry.countries.get(alpha_2=country_code).alpha_3


def _read_eurostat_tsv(path: str, index_names: list[str]) -> pd.DataFrame:
    """Read Eurostat TSV data from old bulk files or the SDMX API TSV format."""
    df = pd.read_csv(path, delimiter="\t", index_col=0)
    index = df.index.str.split(",", expand=True)

    if index.nlevels == len(index_names) + 1:
        index = index.rename(["freq", *index_names])
        df.index = index
        if "A" not in df.index.get_level_values("freq"):
            raise ValueError(f"Eurostat file {path} does not contain annual data.")
        df = df.xs("A", level="freq")
    elif index.nlevels == len(index_names):
        df.index = index.rename(index_names)
    else:
        raise ValueError(
            f"Unexpected Eurostat index format in {path}: "
            f"expected {len(index_names)} or {len(index_names) + 1} fields, "
            f"found {index.nlevels}."
        )

    df.columns = pd.Index([int(str(col).strip()) for col in df.columns], name="year")
    return df


def _eurostat_values_to_numeric(df: pd.DataFrame) -> pd.DataFrame:
    return df.apply(
        lambda column: pd.to_numeric(
            column.astype(str).str.strip().str.split().str[0], errors="coerce"
        )
    )


def get_heat_demand(
    path_to_hh_end_use: str,
    path_to_ch_end_use: str,
    path_to_energy_balance: str,
    path_to_commercial_demand: str,
    path_to_shapes: str,
    path_to_population: str | None,
    paths_to_ecuk_end_use: list[str],
    paths_to_uk_jrc_idees_2015: list[str],
    path_to_carrier_names: str,
    heat_tech_params: dict[str, dict[str, float]],
    data_proxies: dict[str, dict[str, list[str]]],
    country_codes: list[str],
    source_country_codes: list[str],
    model_years: list[int],
    path_to_electricity_demand: str,
    path_to_output: str,
) -> None:
    """Get the annual heat demand of countries in TWh."""
    model_years = [int(year) for year in model_years]
    data_proxies = data_proxies or {}
    # Get annual energy balance data for household and commercial sectors
    energy_balance_dfs = _get_energy_balances(
        path_to_energy_balance, path_to_carrier_names
    )
    if paths_to_ecuk_end_use:
        energy_balance_dfs = _add_gbr_official_energy_balances(
            energy_balance_dfs, paths_to_ecuk_end_use[0]
        )
    annual_energy_balance_proxies = data_proxies.get("annual_energy_balance", {})
    if set(country_codes) & set(annual_energy_balance_proxies):
        if path_to_population is None:
            raise ValueError(
                "Annual energy-balance proxies require shape population data."
            )
        country_population = _shape_country_population(
            path_to_shapes, path_to_population
        )
        energy_balance_dfs = {
            sector: _add_energy_balance_proxies(
                df, country_population, annual_energy_balance_proxies, country_codes
            )
            for sector, df in energy_balance_dfs.items()
        }
    energy_balance_dfs = {
        sector: _select_model_years(df, model_years, f"{sector} energy balance")
        for sector, df in energy_balance_dfs.items()
    }

    # Get household final energy demand by end use
    annual_final_demand = _get_household_final_energy_demand(
        path_to_hh_end_use,
        path_to_ch_end_use,
        path_to_carrier_names,
        paths_to_ecuk_end_use,
    )
    annual_final_demand = _add_household_end_use_proxies(
        annual_final_demand,
        energy_balance_dfs["hh"],
        data_proxies.get("household_end_use", {}),
        country_codes,
    )

    # get commercial final energy demand by end use
    annual_final_demand = get_commercial_final_energy_demand(
        energy_balance_dfs["com"].add(energy_balance_dfs["oth"], fill_value=0),
        path_to_ch_end_use,
        path_to_commercial_demand,
        paths_to_ecuk_end_use,
        paths_to_uk_jrc_idees_2015,
        annual_final_demand,
        jrc_idees_proxies=data_proxies.get("jrc_idees", {}),
        country_codes=country_codes,
        source_country_codes=source_country_codes,
    )

    # get electricity demand data specifically, to remove from ENTSOE timeseries
    electricity_demand = get_annual_electricity_demand(
        annual_final_demand, energy_balance_dfs
    )
    # Convert final to useful energy demand
    national_useful_heat_demand = get_national_useful_heat_demand(
        annual_final_demand, energy_balance_dfs, heat_tech_params
    )

    # Fill remaining values before saving and filter to country scope
    for df, path in zip(
        [electricity_demand, national_useful_heat_demand],
        [path_to_electricity_demand, path_to_output],
    ):
        result = (
            _select_output_years(df, model_years)
            .stack([0, 1], future_stack=True)
            .squeeze()
        )
        result = result.loc[
            result.index.get_level_values("country_code").isin(country_codes)
        ]
        result.rename("value").pipe(_check_no_remaining_missing_values).to_csv(path)


def _get_energy_balances(
    path_to_energy_balance: str, path_to_carrier_names: str
) -> dict[str, pd.DataFrame]:
    energy_balance_df = pd.read_csv(
        path_to_energy_balance,
        index_col=["cat_code", "carrier_code", "unit", "country", "year"],
        header=0,
    ).squeeze()
    carrier_names_df = pd.read_csv(path_to_carrier_names, index_col=0, header=0)

    balance_codes = {
        "hh": ["FC_OTH_HH_E"],
        "com": ["FC_OTH_CP_E"],
        "oth": ["FC_OTH_AF_E", "FC_OTH_FISH_E", "FC_OTH_NSP_E"],
    }
    balances = {
        sector_code: _slice_energy_balance_by_sector(
            sector_code=sector_code,
            df=energy_balance_df,
            carrier_names_df=carrier_names_df,
            cat_codes=cat_codes,
        )
        for sector_code, cat_codes in balance_codes.items()
    }
    return balances


def _add_gbr_official_energy_balances(
    energy_balance_dfs: dict[str, pd.DataFrame], path_to_ecuk_end_use: str
) -> dict[str, pd.DataFrame]:
    energy_balance_dfs = energy_balance_dfs.copy()
    for sector_code, ecuk_sector in {"hh": "Domestic", "com": "Services"}.items():
        official_balance = read_ecuk_sector_energy_balance(
            path_to_ecuk_end_use, ecuk_sector
        )
        energy_balance_dfs[sector_code] = _update_country_years(
            energy_balance_dfs[sector_code], official_balance
        )
    return energy_balance_dfs


def _update_country_years(
    base: pd.DataFrame, replacement: pd.DataFrame
) -> pd.DataFrame:
    result = base.copy()
    result = result.reindex(
        columns=sorted(set(result.columns) | set(replacement.columns))
    )
    missing_index = replacement.index.difference(result.index)
    if not missing_index.empty:
        result = pd.concat(
            [
                result,
                pd.DataFrame(index=missing_index, columns=result.columns, dtype=float),
            ]
        )
    result.loc[replacement.index, replacement.columns] = replacement
    return result.sort_index()


def _select_model_years(
    df: pd.DataFrame, model_years: list[int], label: str
) -> pd.DataFrame:
    missing_years = sorted(set(model_years) - set(df.columns))
    if missing_years:
        raise ValueError(f"{label} is missing configured model years: {missing_years}")
    return df.loc[:, model_years]


def _select_output_years(df: pd.DataFrame, model_years: list[int]) -> pd.DataFrame:
    if "year" in df.index.names:
        missing_years = sorted(
            set(model_years) - set(df.index.get_level_values("year"))
        )
        if missing_years:
            raise ValueError(f"Output demand is missing model years: {missing_years}")
        return df.loc[df.index.get_level_values("year").isin(model_years)]
    missing_years = sorted(set(model_years) - set(df.index))
    if missing_years:
        raise ValueError(f"Output demand is missing model years: {missing_years}")
    return df.loc[model_years]


def _normalise_shape_ids(values: pd.Index | pd.Series) -> pd.Index:
    return pd.Index(values.astype(str).str.replace(".", "-", regex=False))


def _shape_country_population(
    path_to_shapes: str, path_to_population: str
) -> pd.Series:
    shapes = pd.read_parquet(path_to_shapes)
    required_columns = {"shape_id", "country_id"}
    missing_columns = required_columns.difference(shapes.columns)
    if missing_columns:
        raise ValueError(f"Missing required shape columns: {sorted(missing_columns)}")

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
        str(country_id).strip().upper(): [
            str(reference).strip().upper() for reference in references
        ]
        for country_id, references in proxy_map.items()
    }


def _add_energy_balance_proxies(
    energy_balance: pd.DataFrame,
    country_population: pd.Series,
    proxies: dict[str, list[str]],
    country_codes: list[str],
) -> pd.DataFrame:
    proxies = _normalise_proxy_map(proxies)
    existing_countries = set(energy_balance.index.get_level_values("country_code"))
    additions = []
    for country_code in country_codes:
        if country_code in existing_countries:
            continue
        reference_countries = proxies[country_code]
        proxy_population = country_population.loc[[country_code, *reference_countries]]
        if not (proxy_population > 0).all():
            missing_population = sorted(proxy_population[proxy_population <= 0].index)
            raise ValueError(f"Missing proxy population: {missing_population}.")
        reference_intensities = []
        for reference in reference_countries:
            reference_balance = energy_balance.xs(
                reference, level="country_code", drop_level=False
            ).droplevel("country_code")
            reference_intensities.append(
                reference_balance.div(proxy_population[reference])
            )

        proxied_balance = (
            pd.concat(reference_intensities)
            .groupby(level="carrier_name")
            .mean()
            .mul(proxy_population[country_code])
        )
        proxied_balance = (
            proxied_balance.assign(country_code=country_code)
            .set_index("country_code", append=True)
            .reorder_levels(energy_balance.index.names)
        )
        additions.append(proxied_balance)

    if additions:
        energy_balance = pd.concat([energy_balance, *additions]).sort_index()
    return energy_balance


def _slice_energy_balance_by_sector(
    df: pd.DataFrame,
    carrier_names_df: pd.DataFrame,
    cat_codes: list[str],
    sector_code: str,
) -> pd.DataFrame:
    relevant_carriers = (
        carrier_names_df[f"{sector_code}_carrier_name"].dropna().drop_duplicates()
    )
    countries = df.index.get_level_values("country").unique()
    years = df.index.get_level_values("year").unique().sort_values()
    relevant_index = pd.MultiIndex.from_product(
        [relevant_carriers, countries], names=["carrier_code", "country"]
    )

    available_cat_codes = [
        cat_code
        for cat_code in cat_codes
        if cat_code in df.index.get_level_values("cat_code")
    ]
    if not available_cat_codes:
        return (
            pd.DataFrame(0, index=relevant_index, columns=years)
            .rename_axis(["carrier_name", "country_code"], axis=0)
            .rename_axis("year", axis=1)
        )

    df = df.loc[available_cat_codes]  # cat_code is always the first element

    assert df.index.get_level_values("unit").unique().tolist() == ["TJ"], (
        "There are other units than TJ in the energy balance data. This is not expected."
    )

    df = (
        df.xs("TJ", level="unit")
        .mul(TJ_TO_TWH)  # TJ -> TWh
        .unstack("year")
        .rename(
            index=carrier_names_df[f"{sector_code}_carrier_name"].dropna().to_dict(),
            level="carrier_code",
        )
        .groupby(by=["carrier_code", "country"], level=["carrier_code", "country"])
        .sum()
        .reindex(relevant_index, fill_value=0)
        .reindex(columns=years, fill_value=0)
        .rename_axis(["carrier_name", "country_code"], axis=0)
    )
    return df


def _get_household_final_energy_demand(
    path_to_hh_end_use: str,
    path_to_ch_end_use: str,
    path_to_carrier_names: str,
    paths_to_ecuk_end_use: list[str],
) -> pd.DataFrame:
    """Read data on household final energy demand."""
    carrier_names_df = pd.read_csv(path_to_carrier_names, index_col=0, header=0)

    hh_end_use_df = _read_eurostat_tsv(
        path_to_hh_end_use,
        index_names=["cat_code", "carrier_code", "unit", "country_code"],
    )

    # remove 'countries' which are not relevant
    not_countries = [
        c
        for c in hh_end_use_df.index.get_level_values("country_code").unique()
        if len(c) > 2
    ] + ["XK"]
    hh_end_use_df = hh_end_use_df.drop(
        axis=0, level="country_code", labels=not_countries
    )

    assert _check_units_removed(hh_end_use_df, carrier_names_df), (
        "Check that you can slice by 'TJ' only, some other units in the hh_end_use data might be relevant."
    )  # noqa: E501

    # Just keep relevant data
    hh_end_use_df = (
        _eurostat_values_to_numeric(hh_end_use_df.xs("TJ", level="unit"))
        .mul(TJ_TO_TWH)  # TJ -> TWh
        .dropna(how="all")
    )

    # clean up renewables info
    hh_end_use_df = update_final_renewable_energy_demand(hh_end_use_df)

    country_codes_ = {
        c: eurostat_to_alpha3(c)
        for c in hh_end_use_df.index.get_level_values("country_code")
    }

    # Add missing renewables data to
    # rename index labels to be more readable

    hh_end_use_df = hh_end_use_df.groupby(
        [
            END_USE_CAT_NAMES,
            carrier_names_df["hh_carrier_name"].dropna().to_dict(),
            country_codes_,
        ],
        level=["cat_code", "carrier_code", "country_code"],
    ).sum()
    hh_end_use_df.index = hh_end_use_df.index.rename(
        ["end_use", "carrier_name"], level=["cat_code", "carrier_code"]
    )

    # Add Swiss data
    ch_hh_end_use_df = read_ch_hh_final_demand(path_to_ch_end_use)
    hh_end_use_df = pd.concat([hh_end_use_df, ch_hh_end_use_df], sort=True)
    if paths_to_ecuk_end_use:
        hh_end_use_df = _update_country_years(
            hh_end_use_df, read_ecuk_domestic_final_demand(paths_to_ecuk_end_use[0])
        )

    # Clean up data
    hh_end_use_df = (
        hh_end_use_df.sort_index()
        .where(hh_end_use_df > 0)
        .dropna(how="all")
        .assign(cat_name="household")
        .set_index("cat_name", append=True)
    )

    return hh_end_use_df


def _add_household_end_use_proxies(
    household_demand: pd.DataFrame,
    household_energy_balance: pd.DataFrame,
    proxies: dict[str, list[str]],
    country_codes: list[str],
) -> pd.DataFrame:
    proxies = _normalise_proxy_map(proxies)
    existing_countries = set(household_demand.index.get_level_values("country_code"))
    additions = []
    for country_code in country_codes:
        if country_code in existing_countries:
            continue
        reference_countries = proxies.get(country_code)
        if not reference_countries:
            raise ValueError(
                "Missing household end-use data for "
                f"{country_code}, and no household proxy is configured."
            )
        missing_reference_countries = sorted(
            set(reference_countries) - existing_countries
        )
        if missing_reference_countries:
            raise ValueError(
                f"Household end-use proxy for {country_code} references countries "
                "without household end-use data: "
                f"{missing_reference_countries}. Available proxy source countries: "
                f"{sorted(existing_countries)}."
            )
        target_balance = household_energy_balance.xs(
            country_code, level="country_code", drop_level=False
        )
        target_balance = _split_household_proxy_electricity_balance(
            target_balance, household_demand, reference_countries
        )
        proxy_shares = _household_proxy_end_use_shares(
            household_demand, reference_countries
        )
        proxy_shares = (
            proxy_shares.to_frame("value")
            .assign(country_code=country_code)
            .set_index("country_code", append=True)["value"]
            .reorder_levels(
                ["end_use", "carrier_name", "country_code", "cat_name", "year"]
            )
        )
        proxied_demand = _map_end_use_shares_to_eurostat(
            target_balance, proxy_shares
        ).unstack("year")
        if proxied_demand.empty:
            raise ValueError(
                f"Household end-use proxy for {country_code} produced no demand "
                "for the configured model years."
            )
        additions.append(proxied_demand)

    if additions:
        household_demand = pd.concat([household_demand, *additions]).sort_index()
    return household_demand


def _split_household_proxy_electricity_balance(
    target_balance: pd.DataFrame,
    household_demand: pd.DataFrame,
    reference_countries: list[str],
) -> pd.DataFrame:
    carriers = target_balance.index.get_level_values("carrier_name")
    if "electricity" not in carriers or (
        set(HOUSEHOLD_ELECTRIC_PROXY_CARRIERS) - {"electricity"}
    ) & set(carriers):
        return target_balance

    shares = _household_proxy_carrier_shares(
        household_demand, reference_countries, HOUSEHOLD_ELECTRIC_PROXY_CARRIERS
    )
    shares = shares.reindex(columns=target_balance.columns).dropna(axis=1, how="all")
    if shares.empty:
        return target_balance

    target_electricity = target_balance.xs("electricity", level="carrier_name")
    split_balance = pd.concat(
        [
            target_electricity.mul(shares.loc[carrier], axis=1)
            .assign(carrier_name=carrier)
            .set_index("carrier_name", append=True)
            for carrier in shares.index
        ]
    ).reorder_levels(target_balance.index.names)

    non_electricity_balance = target_balance.drop("electricity", level="carrier_name")
    return pd.concat([non_electricity_balance, split_balance]).sort_index()


def _household_proxy_carrier_shares(
    household_demand: pd.DataFrame, reference_countries: list[str], carriers: list[str]
) -> pd.DataFrame:
    reference_demand = pd.concat(
        [
            household_demand.xs(reference, level="country_code", drop_level=False)
            for reference in reference_countries
        ]
    )
    reference_demand.columns = reference_demand.columns.rename("year")
    carrier_totals = (
        reference_demand.stack()
        .loc[lambda s: s.index.get_level_values("carrier_name").isin(carriers)]
        .groupby(level=["carrier_name", "year"])
        .sum()
        .unstack("year")
    )
    carrier_totals = carrier_totals.where(carrier_totals > 0)
    return carrier_totals.div(carrier_totals.sum(axis=0), axis=1).dropna(how="all")


def _household_proxy_end_use_shares(
    household_demand: pd.DataFrame, reference_countries: list[str]
) -> pd.Series:
    reference_demand = pd.concat(
        [
            household_demand.xs(reference, level="country_code", drop_level=False)
            for reference in reference_countries
        ]
    )
    reference_demand.columns = reference_demand.columns.rename("year")
    reference_demand = reference_demand.stack("year")
    denominator = reference_demand.groupby(
        level=["carrier_name", "country_code", "cat_name", "year"]
    ).transform("sum")
    shares = reference_demand.div(denominator).dropna()
    return (
        shares.groupby(level=["end_use", "carrier_name", "cat_name", "year"])
        .mean()
        .rename("value")
    )


def _check_units_removed(df: pd.DataFrame, carrier_names_df: pd.DataFrame) -> bool:
    cat_codes = [
        cat_code
        for cat_code in END_USE_CAT_NAMES
        if cat_code in df.index.get_level_values("cat_code")
    ]
    carrier_codes = [
        carrier_code
        for carrier_code in carrier_names_df["hh_carrier_name"].dropna().index
        if carrier_code in df.index.get_level_values("carrier_code")
    ]
    if not cat_codes or not carrier_codes:
        return True

    df = (  # first re-organise df
        df.stack("year").unstack("unit").loc[idx[cat_codes, carrier_codes, :, :], :]
    )
    if "TJ" not in df.columns:
        return len(df) == 0

    # check that when 'TJ' is NaN, the other values are also NaN.
    # Otherwise, we are missing some data. Print the df below to check.

    numeric_df = _eurostat_values_to_numeric(df)
    df = numeric_df[
        numeric_df["TJ"].isna()
        & ~(numeric_df.drop("TJ", axis=1).fillna(0) == 0).all(axis=1)
    ]
    return len(df) == 0


def update_final_renewable_energy_demand(df: pd.DataFrame) -> pd.DataFrame:
    """Rescale renewable carrier sum so that it matches demand.

    Some household final energy data has a higher overall renewables energy demand
    (RA000) than the sum of renewable energy carriers. Here we scale all renewable
    energy carriers evenly, to match the total of RA000.
    """

    def _get_rows_to_update(df):
        renewables = (
            df.stack()
            .unstack("carrier_code")
            .filter(regex="^R")
            .where(lambda x: x > 0)
            .dropna(how="all")
        )
        if renewables.empty or "RA000" not in renewables.columns:
            return renewables.iloc[0:0], pd.Series(dtype=float)
        renewables_carriers = renewables.drop("RA000", axis=1, errors="ignore").sum(
            axis=1, min_count=1
        )
        renewables_all = renewables.xs("RA000", axis=1)
        # Only update those rows where the sum of renewable energy carriers != RA000
        return (
            renewables.loc[~np.isclose(renewables_carriers, renewables_all)],
            renewables_carriers,
        )

    to_update, renewables_carriers = _get_rows_to_update(df)
    # Some rows have no data other than RA000, so we need to assign that data to one of
    # the renewable energy carriers. We choose biofuels here (R5110-5150_W6000RI)
    completely_missing = renewables_carriers[
        renewables_carriers.isna()
    ].index.intersection(to_update.index)
    to_update.loc[completely_missing, "R5110-5150_W6000RI"] = to_update.loc[
        completely_missing, "R5110-5150_W6000RI"
    ].fillna(to_update.loc[completely_missing, "RA000"])

    # Now we scale all renewable energy carriers to match RA000
    mismatch = to_update.xs("RA000", axis=1).div(
        to_update.drop("RA000", axis=1).sum(axis=1)
    )
    updated = to_update.drop("RA000", axis=1).mul(mismatch, axis=0)
    assert np.allclose(updated.sum(axis=1), to_update.xs("RA000", axis=1))

    updated_reordered = updated.stack().unstack("year").reorder_levels(df.index.names)
    # Add new rows
    df = pd.concat(
        [df, updated_reordered.loc[updated_reordered.index.difference(df.index)]]
    )
    # Update existing rows
    df.update(updated_reordered)
    # Ensure everything has been updated as expected
    assert _get_rows_to_update(df)[0].empty
    return df


def read_ch_hh_final_demand(path_to_ch_end_use: str) -> pd.DataFrame:
    """Get Switzerland data from their govt. stats documents."""
    space_heat = _get_ch_sheet(
        path_to_ch_end_use, "Tabelle20", translation=CH_ENERGY_CARRIER_TRANSLATION
    )
    hot_water = _get_ch_sheet(
        path_to_ch_end_use, "Tabelle22", translation=CH_ENERGY_CARRIER_TRANSLATION
    )
    cooking = _get_ch_sheet(
        path_to_ch_end_use, "Tabelle23", translation=CH_ENERGY_CARRIER_TRANSLATION
    )

    df = (
        pd.concat(
            [space_heat, hot_water, cooking],
            keys=("space_heat", "hot_water", "cooking"),
            names=["end_use", "carrier_name"],
        )
        .assign(country_code="CHE")
        .set_index("country_code", append=True)
    )

    # Columns are years
    df.columns = df.columns.astype(int).rename("year")

    return df * PJ_TO_TWH  # PJ -> TWh


def get_commercial_final_energy_demand(
    energy_balance: pd.DataFrame,
    path_to_ch_end_use: str,
    path_to_jrc_end_use: str,
    paths_to_ecuk_end_use: list[str],
    paths_to_uk_jrc_idees_2015: list[str],
    annual_final_energy_demand: pd.DataFrame,
    jrc_idees_proxies: dict[str, list[str]],
    country_codes: list[str],
    source_country_codes: list[str],
) -> pd.DataFrame:
    """Get commercial final energy demand.

    Use JRC IDEES service sector final energy demand to estimate demand for
    space heating and hot water in the commercial sector across all countries.
    Add Swiss data from Swiss govt. stats.
    """
    jrc_end_use_df = _read_processed_jrc_final_energy(path_to_jrc_end_use)

    # Map JRC end uses to annual commercial demand
    mapped_end_uses = _map_jrc_to_eurostat(
        energy_balance, jrc_end_use_df, jrc_idees_proxies, country_codes
    )

    official_commercial_data = []
    if "GBR" in source_country_codes:
        if not paths_to_ecuk_end_use:
            raise ValueError(
                "GBR is in scope, but no ECUK end-use workbook was provided."
            )
        official_commercial_data.append(
            _map_uk_official_to_eurostat(
                energy_balance, paths_to_ecuk_end_use[0], paths_to_uk_jrc_idees_2015
            )
        )

    official_commercial_data = [
        official_data.reorder_levels(mapped_end_uses.index.names)
        for official_data in official_commercial_data
    ]
    for official_data in official_commercial_data:
        mapped_end_uses = _drop_matching_country_end_uses(
            mapped_end_uses, official_data
        )

    # Add official data, Swiss data when in scope, and ambient heat from heat pumps.
    mapped_end_use_parts = [mapped_end_uses, *official_commercial_data]
    if "CHE" in source_country_codes:
        # 'fuel' is just generic non-electric energy, which we distribute based on
        # household data
        ch_con_fuel = _read_ch_non_hh_non_electricity_demand(
            path_to_ch_end_use, "Tabelle27", annual_final_energy_demand
        )
        ch_con_elec = _read_ch_non_hh_electricity_demand(
            path_to_ch_end_use, "Tabelle28"
        )
        mapped_end_use_parts.extend(
            [
                ch_con_fuel.rename({"process_heat": "cooking"}).reorder_levels(
                    mapped_end_uses.index.names
                ),
                ch_con_elec.rename({"process_heat": "cooking"}).reorder_levels(
                    mapped_end_uses.index.names
                ),
            ]
        )

    mapped_end_use_parts.append(
        energy_balance.loc[  # JRC data only refers to heat pumps for heating in space heating  # noqa: E501
            ["ambient_heat"]
        ]
        .assign(end_use="space_heat")
        .set_index("end_use", append=True)
        .stack()
        .reorder_levels(mapped_end_uses.index.names)
    )
    mapped_end_uses = pd.concat(mapped_end_use_parts)
    mapped_end_uses.index = mapped_end_uses.index.remove_unused_levels()
    mapped_end_uses = _add_commercial_end_use_proxies(
        mapped_end_uses, energy_balance, jrc_idees_proxies, country_codes
    )

    annual_final_energy_demand = pd.concat(
        [
            annual_final_energy_demand,
            mapped_end_uses.where(mapped_end_uses > 0)
            .dropna()
            .unstack("year")
            .reindex(columns=annual_final_energy_demand.columns)
            .assign(cat_name="commercial")
            .set_index("cat_name", append=True)
            .reorder_levels(annual_final_energy_demand.index.names),
        ]
    ).sort_index()

    return annual_final_energy_demand


def _read_processed_jrc_final_energy(path_to_jrc_end_use: str) -> pd.Series:
    jrc_end_use = pd.read_csv(path_to_jrc_end_use, index_col=JRC_HEAT_INDEX_NAMES)
    jrc_end_use = pd.to_numeric(jrc_end_use.squeeze("columns"), errors="coerce")
    if jrc_end_use.empty:
        return _empty_processed_jrc_final_energy()
    return jrc_end_use.xs("final_energy", level="energy")


def _empty_processed_jrc_final_energy() -> pd.Series:
    index = pd.MultiIndex.from_arrays(
        [[] for _ in JRC_FINAL_ENERGY_INDEX_NAMES], names=JRC_FINAL_ENERGY_INDEX_NAMES
    )
    return pd.Series(index=index, dtype=float)


def _add_commercial_end_use_proxies(
    commercial_demand: pd.Series,
    energy_balance: pd.DataFrame,
    proxies: dict[str, list[str]],
    country_codes: list[str],
) -> pd.Series:
    proxies = _normalise_proxy_map(proxies)
    if not proxies:
        return commercial_demand

    core_country_index = commercial_demand.loc[
        commercial_demand.index.get_level_values("carrier_name") != "ambient_heat"
    ].index.get_level_values("country_code")
    countries_with_core_data = set(core_country_index)
    additions = []
    added_countries = []
    for country_code in country_codes:
        if country_code in countries_with_core_data:
            continue
        reference_countries = proxies.get(country_code)
        if not reference_countries:
            raise ValueError(
                "Missing commercial end-use data for "
                f"{country_code}, and no JRC-IDEES proxy is configured."
            )
        missing_reference_countries = sorted(
            set(reference_countries) - countries_with_core_data
        )
        if missing_reference_countries:
            raise ValueError(
                f"Commercial end-use proxy for {country_code} references countries "
                "without commercial end-use data: "
                f"{missing_reference_countries}. Available proxy source countries: "
                f"{sorted(countries_with_core_data)}."
            )
        proxy_shares = _commercial_proxy_end_use_shares(
            commercial_demand, reference_countries
        )
        proxy_shares = (
            proxy_shares.to_frame("value")
            .assign(country_code=country_code)
            .set_index("country_code", append=True)["value"]
            .reorder_levels(commercial_demand.index.names)
        )
        target_balance = energy_balance.xs(
            country_code, level="country_code", drop_level=False
        )
        proxied_demand = _map_end_use_shares_to_eurostat(target_balance, proxy_shares)
        if proxied_demand.empty:
            raise ValueError(
                f"Commercial end-use proxy for {country_code} produced no demand "
                "for the configured model years."
            )
        additions.append(proxied_demand)
        added_countries.append(country_code)

    if not additions:
        return commercial_demand

    keep_base = ~commercial_demand.index.get_level_values("country_code").isin(
        added_countries
    )
    return pd.concat([commercial_demand.loc[keep_base], *additions]).sort_index()


def _commercial_proxy_end_use_shares(
    commercial_demand: pd.Series, reference_countries: list[str]
) -> pd.Series:
    reference_demand = pd.concat(
        [
            commercial_demand.xs(reference, level="country_code", drop_level=False)
            for reference in reference_countries
        ]
    )
    denominator = reference_demand.groupby(
        level=["carrier_name", "country_code", "year"]
    ).transform("sum")
    shares = reference_demand.div(denominator).dropna()
    return (
        shares.groupby(level=["end_use", "carrier_name", "year"]).mean().rename("value")
    )


def _map_ecuk_to_eurostat(energy_balance: pd.DataFrame, path_to_ecuk_end_use: str):
    ecuk_end_use_percent = read_ecuk_service_end_use_shares(
        path_to_ecuk_end_use, energy_balance.columns
    )
    return _map_end_use_shares_to_eurostat(energy_balance, ecuk_end_use_percent)


def _map_uk_official_to_eurostat(
    energy_balance: pd.DataFrame,
    path_to_ecuk_end_use: str,
    paths_to_uk_jrc_idees_2015: list[str],
) -> pd.Series:
    uk_end_use_percent = _uk_official_end_use_shares(
        path_to_ecuk_end_use, paths_to_uk_jrc_idees_2015, energy_balance.columns
    )
    return _map_end_use_shares_to_eurostat(energy_balance, uk_end_use_percent)


def _uk_official_end_use_shares(
    path_to_ecuk_end_use: str,
    paths_to_uk_jrc_idees_2015: list[str],
    target_years: pd.Index,
) -> pd.Series:
    target_years = sorted(int(year) for year in target_years)
    if not target_years:
        return pd.Series(dtype=float)

    ecuk_years = [year for year in target_years if year >= 2017]
    legacy_years = [year for year in target_years if year <= 2015]
    unsupported_years = [year for year in target_years if year == 2016]
    if unsupported_years:
        raise ValueError(
            "GBR commercial end-use shares are unavailable for exact years: "
            f"{unsupported_years}."
        )

    pieces = []
    if ecuk_years:
        pieces.append(
            read_ecuk_service_end_use_shares(path_to_ecuk_end_use, ecuk_years)
        )

    if legacy_years:
        if not paths_to_uk_jrc_idees_2015:
            raise ValueError(
                "GBR annual demand before 2020 requires the legacy UK "
                "JRC-IDEES 2015 workbook."
            )
        legacy_shares = _read_uk_jrc_2015_end_use_shares(paths_to_uk_jrc_idees_2015[0])
        pieces.append(
            _select_legacy_uk_jrc_shares_for_years(legacy_shares, legacy_years)
        )

    if not pieces:
        raise ValueError("Could not derive official UK commercial end-use shares.")

    return pd.concat(pieces).sort_index()


def _read_uk_jrc_2015_end_use_shares(path_to_uk_jrc_idees_2015: str) -> pd.Series:
    jrc_end_use_df = read_jrc_heat_tertiary_sector_data([path_to_uk_jrc_idees_2015]).xs(
        "final_energy", level="energy"
    )
    return (
        _jrc_end_use_percent(jrc_end_use_df)
        .xs("GBR", level="country_code", drop_level=False)
        .reorder_levels(["carrier_name", "end_use", "country_code", "year"])
        .sort_index()
    )


def _select_legacy_uk_jrc_shares_for_years(
    legacy_shares: pd.Series, target_years: list[int]
) -> pd.Series:
    available_years = sorted(legacy_shares.index.get_level_values("year").unique())
    missing_years = sorted(set(target_years) - set(available_years))
    if missing_years:
        raise ValueError(
            "Legacy UK JRC-IDEES shares are missing requested model years: "
            f"{missing_years}. Available years: {available_years}."
        )
    pieces = []
    for target_year in target_years:
        pieces.append(legacy_shares.xs(target_year, level="year", drop_level=False))
    return pd.concat(pieces)


def _drop_matching_country_end_uses(
    base: pd.Series, replacement: pd.Series
) -> pd.Series:
    countries = set(replacement.index.get_level_values("country_code"))
    end_uses = set(replacement.index.get_level_values("end_use"))
    mask = base.index.get_level_values("country_code").isin(countries)
    mask &= base.index.get_level_values("end_use").isin(end_uses)
    return base.loc[~mask]


def _map_jrc_to_eurostat(
    energy_balance: pd.DataFrame,
    jrc_end_use_df: pd.DataFrame,
    jrc_idees_proxies: dict[str, list[str]],
    country_codes: list[str],
) -> pd.DataFrame:
    jrc_end_use_percent = _jrc_end_use_percent(jrc_end_use_df)

    mapped_end_uses = _map_end_use_shares_to_eurostat(
        energy_balance, jrc_end_use_percent
    )

    return mapped_end_uses


def _map_end_use_shares_to_eurostat(
    energy_balance: pd.DataFrame, end_use_percent: pd.Series
) -> pd.Series:
    energy_balance_long = energy_balance.stack()
    energy_balance_long.index = energy_balance_long.index.set_names(
        ["carrier_name", "country_code", "year"]
    )
    lookup_index = pd.MultiIndex.from_arrays(
        [
            end_use_percent.index.get_level_values("carrier_name"),
            end_use_percent.index.get_level_values("country_code"),
            end_use_percent.index.get_level_values("year"),
        ],
        names=energy_balance_long.index.names,
    )
    energy_for_share = pd.Series(
        energy_balance_long.reindex(lookup_index).to_numpy(),
        index=end_use_percent.index,
    )
    return energy_for_share.mul(end_use_percent).dropna()


def _jrc_end_use_percent(jrc_end_use_df: pd.Series) -> pd.Series:
    if jrc_end_use_df.empty:
        return _empty_end_use_share_series()

    jrc_end_use_df = (
        jrc_end_use_df.xs("ktoe", level="unit")
        .rename(eurostat_to_alpha3, level="country_code")
        .mul(KTOE_TO_TWH)  # kTOE -> TWh
    )
    jrc_end_use_percent = (
        jrc_end_use_df.div(jrc_end_use_df.unstack("end_use").sum(axis=1))
        .unstack("year")
        .dropna(how="all")
        .stack("year")
    )
    return jrc_end_use_percent


def _empty_end_use_share_series() -> pd.Series:
    index = pd.MultiIndex.from_arrays(
        [[] for _ in END_USE_SHARE_INDEX_NAMES], names=END_USE_SHARE_INDEX_NAMES
    )
    return pd.Series(index=index, dtype=float)


def _read_ch_non_hh_electricity_demand(
    path_to_ch_end_use: str, sheet_name: str
) -> pd.DataFrame:
    return (
        _get_ch_sheet(
            path_to_ch_end_use, sheet_name, translation=CH_HH_END_USE_TRANSLATION
        )
        .assign(carrier_name="electricity", country_code="CHE")
        .set_index(["country_code", "carrier_name"], append=True)
        .stack()
        .rename_axis(index=["end_use", "country_code", "carrier_name", "year"])
        .mul(PJ_TO_TWH)
    )


def _read_ch_non_hh_non_electricity_demand(
    path_to_ch_end_use: str, sheet_name: str, hh_final_energy_demand: pd.DataFrame
):
    ch_con = _get_ch_sheet(
        path_to_ch_end_use, sheet_name, translation=CH_HH_END_USE_TRANSLATION
    )
    # this is actually just generic non-electric energy,
    # which we assign to fuels using household ratios
    # ASSUME Swiss carrier ratios in commerce are the same as in households
    hh_con = hh_final_energy_demand.xs(
        ("CHE", "household"), level=("country_code", "cat_name")
    )
    hh_ratios = hh_con.div(
        hh_con.drop("electricity", level="carrier_name", errors="ignore")
        .groupby(level=["end_use"])
        .sum(),
        axis=0,
    ).reindex(columns=ch_con.columns)
    ch_con_disaggregated = hh_ratios.mul(ch_con, level="end_use", axis=0).dropna(
        how="all"
    )

    assert np.allclose(
        ch_con_disaggregated.groupby(level="end_use").sum().reindex_like(ch_con), ch_con
    )
    ch_con = ch_con_disaggregated.reset_index("carrier_name")
    return (
        ch_con.assign(country_code="CHE")
        .set_index(["country_code", "carrier_name"], append=True)
        .stack()
        .rename_axis(index=["end_use", "country_code", "carrier_name", "year"])
        .mul(PJ_TO_TWH)  # PJ -> TWh
    )


def _fill_data_gaps(
    end_use_df: pd.DataFrame, energy_balance_dfs: dict[str, pd.DataFrame], fill: str
) -> pd.DataFrame:
    end_use_df = end_use_df.where(end_use_df > 0)

    # Fill the household sector's end-use data based on total sectoral demand
    hh_country_energy_balance = (
        energy_balance_dfs["hh"]
        .groupby(level="country_code")
        .sum()
        .stack()
        .where(lambda x: x > 0)
    )

    if (
        isinstance(end_use_df.columns, pd.MultiIndex)
        and "cat_name" in end_use_df.columns.names
        and "household" in end_use_df.columns.get_level_values("cat_name")
    ):
        household_columns = (
            end_use_df.columns.get_level_values("cat_name") == "household"
        )
        household_demand = end_use_df.loc[:, household_columns]
        end_use_df.loc[:, household_columns] = household_demand.fillna(
            household_demand.div(hh_country_energy_balance, axis=0)
            .groupby(level="country_code")
            .mean()
            .mul(hh_country_energy_balance, level="country_code", axis=0)
        )

    end_use_df = end_use_df.where(end_use_df > 0)

    # For all remaining gaps, fill with mean/first/last/max/min/whatever data for the
    # country, based on the string given by 'fill'
    end_use_df = end_use_df.fillna(end_use_df.groupby("country_code").agg(fill))

    return end_use_df


def get_annual_electricity_demand(
    annual_final_energy_demand: pd.DataFrame,
    energy_balance_dfs: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    """Get annual energy demand coming from electricity.

    To remove later from the electricity demand profile.
    Gaps in electricity demand are filled before saving, based on annual energy demand.
    """
    electricity_carriers = ["electricity", "direct_electric", "heat_pump"]
    demand = annual_final_energy_demand.drop("end_use_electricity", errors="ignore")
    demand = demand.loc[
        demand.index.get_level_values("carrier_name").isin(electricity_carriers)
    ]
    electricity_demand = (
        demand.groupby(level=["end_use", "country_code", "cat_name"])
        .sum()
        .stack()
        .unstack(["end_use", "cat_name"])
    )

    electricity_demand = _fill_data_gaps(
        electricity_demand, energy_balance_dfs, fill="first"
    )
    return electricity_demand


def get_national_useful_heat_demand(
    annual_final_energy_demand: pd.DataFrame,
    energy_balance_dfs: dict[str, pd.DataFrame],
    heat_tech_params: dict[str, dict[str, float]],
) -> pd.DataFrame:
    """Derive useful heat demand from final energy demand."""
    demands = []
    for end_use in ["space_heat", "hot_water", "cooking"]:
        if end_use not in annual_final_energy_demand.index.get_level_values("end_use"):
            continue
        _demand = (
            annual_final_energy_demand.loc[[end_use]]
            .mul(_efficiencies(heat_tech_params[end_use]), level="carrier_name", axis=0)
            .groupby(level=["end_use", "country_code", "cat_name"])
            .sum()
        )
        # sense check: useful demand is at least the minimum efficiency * final demand
        assert (
            (
                _demand
                >= (
                    annual_final_energy_demand.loc[[end_use]]
                    .mul(_efficiencies(heat_tech_params[end_use]).min())
                    .groupby(level=["end_use", "country_code", "cat_name"])
                    .sum()
                )
            )
            .all()
            .all()
        )
        demands.append(_demand)

    if not demands:
        return pd.DataFrame()

    demand = pd.concat(demands).stack().unstack(["end_use", "cat_name"])

    # Fill gaps in demand using energy balance data
    # This is done by finding the average contribution of cooking, space heating
    # and water heating to HH demand (from energy balances),
    # then applying to years in which HH sub class data is not available.
    demand = _fill_data_gaps(demand, energy_balance_dfs, fill="mean")

    return demand


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
            # don't need to deal with heat pump COP if direct electric is 100% efficient
            "direct_electric": 1,
            "heat": 1,
            # heat demand met by heat pumps = heat pump electricity + ambient heat
            "heat_pump": 1,
            "ambient_heat": 1,
        }
    )


def _get_ch_sheet(path_to_excel: str, sheet: str, translation=None) -> pd.DataFrame:
    available_sheets = pd.ExcelFile(path_to_excel).sheet_names
    if sheet not in available_sheets:
        raise ValueError(
            f"Swiss end-use sheet {sheet!r} was not found in {path_to_excel}. "
            "The workflow expects the current BFE end-use workbook "
            "(publication 12361, 2000-2024). Refresh the CHE/end-use.xlsx "
            "automatic resource."
        )

    raw = pd.read_excel(path_to_excel, sheet_name=sheet, header=None)
    header_row, year_columns, years = _ch_sheet_year_columns(raw, sheet)
    label_column = min(year_columns) - 1
    if label_column < 0:
        raise ValueError(f"Could not find row labels in Swiss sheet {sheet}.")

    df = raw.iloc[header_row + 1 :, [label_column, *year_columns]].copy()
    df = df.dropna(how="all")
    df = df.set_index(df.columns[0])
    df.index = df.index.astype(str).str.strip()
    df = df.loc[df.index != ""]
    df.columns = pd.Index(years, name="year")
    if max(years) < 2024:
        raise ValueError(
            f"Swiss end-use sheet {sheet!r} in {path_to_excel} only contains "
            f"years up to {max(years)}. The workflow expects the current BFE "
            "end-use workbook with data through 2024."
        )
    df = df.apply(pd.to_numeric, errors="coerce").dropna(how="all")

    if translation is not None:
        return df.groupby(translation).sum()
    else:
        return df


def _ch_sheet_year_columns(
    raw: pd.DataFrame, sheet_name: str
) -> tuple[int, list[int], list[int]]:
    for row_number, row in raw.iterrows():
        year_columns = []
        years = []
        for column_number, value in row.items():
            try:
                year = int(value)
            except (TypeError, ValueError):
                continue
            if 1900 <= year <= 2100:
                year_columns.append(column_number)
                years.append(year)
        if year_columns:
            return row_number, year_columns, years

    raise ValueError(f"Could not find year columns in Swiss sheet {sheet_name}.")


def _check_no_remaining_missing_values(
    df: pd.Series | pd.DataFrame,
) -> pd.Series | pd.DataFrame:
    missing = df.isna()
    has_missing = (
        missing.any().any() if isinstance(missing, pd.DataFrame) else missing.any()
    )
    if not has_missing:
        return df

    sample = _format_missing_value_sample(df, missing)
    raise ValueError(
        "Annual demand still contains missing values after preprocessing. "
        "Refusing to convert missing demand to zero. Missing entries include:\n"
        f"{sample}"
    )


def _format_missing_value_sample(
    df: pd.Series | pd.DataFrame, missing: pd.Series | pd.DataFrame, n: int = 10
) -> str:
    if isinstance(df, pd.Series):
        sample = df[missing].head(n)
        return sample.index.to_frame(index=False).to_string(index=False)

    row_positions, col_positions = np.where(missing.to_numpy())
    rows = []
    for row_pos, col_pos in zip(row_positions[:n], col_positions[:n]):
        rows.append({"index": df.index[row_pos], "column": df.columns[col_pos]})
    return pd.DataFrame(rows).to_string(index=False)


def _as_list(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    return list(value)


def _as_optional_path(value) -> str | None:
    paths = _as_list(value)
    return paths[0] if paths else None


if __name__ == "__main__":
    get_heat_demand(
        path_to_hh_end_use=snakemake.input.hh_end_use,
        path_to_ch_end_use=snakemake.input.ch_end_use,
        path_to_energy_balance=snakemake.input.energy_balance,
        path_to_commercial_demand=snakemake.input.commercial_demand,
        path_to_shapes=snakemake.input.shapes,
        path_to_population=_as_optional_path(snakemake.input.population),
        paths_to_ecuk_end_use=_as_list(snakemake.input.ecuk_end_use),
        paths_to_uk_jrc_idees_2015=_as_list(snakemake.input.uk_jrc_idees_2015),
        path_to_carrier_names=snakemake.input.carrier_names,
        heat_tech_params=snakemake.params.heat_tech_params,
        country_codes=snakemake.params.countries,
        source_country_codes=snakemake.params.source_countries,
        model_years=snakemake.params.model_years,
        data_proxies=snakemake.params.data_proxies,
        path_to_electricity_demand=snakemake.output.electricity,
        path_to_output=snakemake.output.total_demand,
    )
