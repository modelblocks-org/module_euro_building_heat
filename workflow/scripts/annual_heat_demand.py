"""Calculate annual useful heat demand from energy statistics."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from ecuk_end_use import read_ecuk_service_end_use_shares
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
    paths_to_ecuk_end_use: list[str],
    paths_to_uk_jrc_idees_2015: list[str],
    path_to_carrier_names: str,
    heat_tech_params: dict[str, dict[str, float]],
    fill_missing_values: dict[str, list[str]],
    country_codes: list[str],
    model_years: list[int],
    path_to_electricity_demand: str,
    path_to_output: str,
) -> None:
    """Get the annual heat demand of countries in TWh."""
    model_years = [int(year) for year in model_years]
    # Get annual energy balance data for household and commercial sectors
    energy_balance_dfs = _get_energy_balances(
        path_to_energy_balance, path_to_carrier_names
    )
    energy_balance_dfs = {
        sector: _select_model_years(
            _extend_energy_balance_model_years(df, model_years),
            model_years,
            f"{sector} energy balance",
        )
        for sector, df in energy_balance_dfs.items()
    }

    # Get household final energy demand by end use
    annual_final_demand = _get_household_final_energy_demand(
        path_to_hh_end_use, path_to_ch_end_use, path_to_carrier_names
    )

    # get commercial final energy demand by end use
    annual_final_demand = get_commercial_final_energy_demand(
        energy_balance_dfs["com"].add(energy_balance_dfs["oth"], fill_value=0),
        path_to_ch_end_use,
        path_to_commercial_demand,
        paths_to_ecuk_end_use,
        paths_to_uk_jrc_idees_2015,
        annual_final_demand,
        fill_missing_values=fill_missing_values,
        country_codes=country_codes,
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
        result = _select_output_years(df, model_years).stack([0, 1]).squeeze()
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


def _select_model_years(
    df: pd.DataFrame, model_years: list[int], label: str
) -> pd.DataFrame:
    missing_years = sorted(set(model_years) - set(df.columns))
    if missing_years:
        raise ValueError(f"{label} is missing configured model years: {missing_years}")
    return df.loc[:, model_years]


def _select_output_years(df: pd.DataFrame, model_years: list[int]) -> pd.DataFrame:
    if "year" in df.index.names:
        return df.loc[df.index.get_level_values("year").isin(model_years)]
    return df.reindex(index=model_years)


def _extend_energy_balance_model_years(
    energy_balance: pd.DataFrame, model_years: list[int]
) -> pd.DataFrame:
    """Fill missing country model years from the latest available country year."""
    energy_balance = energy_balance.copy()
    countries = energy_balance.index.get_level_values("country_code").unique()
    for country_code in countries:
        country_balance = energy_balance.xs(
            country_code, level="country_code", drop_level=False
        )
        country_totals = country_balance.sum(axis=0, min_count=1)
        available_years = sorted(
            year
            for year, value in country_totals.items()
            if pd.notna(value) and value > 0
        )
        if not available_years:
            continue

        for model_year in model_years:
            current_total = country_totals.get(model_year)
            if pd.notna(current_total) and current_total > 0:
                continue
            prior_years = [year for year in available_years if year <= model_year]
            source_year = prior_years[-1] if prior_years else available_years[0]
            energy_balance.loc[country_balance.index, model_year] = country_balance[
                source_year
            ].to_numpy()

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
        [relevant_carriers, countries],
        names=["carrier_code", "country"],
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

    assert df.index.get_level_values("unit").unique().tolist() == [
        "TJ"
    ], "There are other units than TJ in the energy balance data. This is not expected."

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
    path_to_hh_end_use: str, path_to_ch_end_use: str, path_to_carrier_names: str
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

    assert _check_units_removed(
        hh_end_use_df, carrier_names_df
    ), "Check that you can slice by 'TJ' only, some other units in the hh_end_use data might be relevant."  # noqa: E501

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

    # Clean up data
    hh_end_use_df = (
        hh_end_use_df.sort_index()
        .where(hh_end_use_df > 0)
        .dropna(how="all")
        .assign(cat_name="household")
        .set_index("cat_name", append=True)
    )

    return hh_end_use_df


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
        df.stack("year")
        .unstack("unit")
        .loc[
            idx[
                cat_codes,
                carrier_codes,
                :,
                :,
            ],
            :,
        ]
    )
    if "TJ" not in df.columns:
        return len(df) == 0

    # check that when 'TJ' is NaN, the other values are also NaN.
    # Otherwise, we are missing some data. Print the df below to check.

    df = df[df["TJ"].isna() & ~(df.drop("TJ", axis=1).fillna(0) == 0).all(axis=1)]
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
        renewables_carriers = renewables.drop(
            "RA000", axis=1, errors="ignore"
        ).sum(axis=1, min_count=1)
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
        path_to_ch_end_use,
        "Tabelle 18",
        skipfooter=8,
        translation=CH_ENERGY_CARRIER_TRANSLATION,
    )
    hot_water = _get_ch_sheet(
        path_to_ch_end_use,
        "Tabelle 20",
        skipfooter=5,
        translation=CH_ENERGY_CARRIER_TRANSLATION,
    )
    # Quirk of the excel is that there is no space in this sheet name
    cooking = _get_ch_sheet(
        path_to_ch_end_use,
        "Tabelle21",
        skipfooter=4,
        translation=CH_ENERGY_CARRIER_TRANSLATION,
    )

    df = (
        pd.concat(
            [space_heat, hot_water, cooking],
            keys=("space_heat", "hot_water", "cooking"),
            names=["cat_name", "carrier_name"],
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
    fill_missing_values: dict[str, list[str]],
    country_codes: list[str],
) -> pd.DataFrame:
    """Get commercial final energy demand.

    Use JRC IDEES service sector final energy demand to estimate demand for
    space heating and hot water in the commercial sector across all countries.
    Add Swiss data from Swiss govt. stats.
    """
    jrc_end_use_df = (
        pd.read_csv(
            path_to_jrc_end_use,
            index_col=[
                "carrier_name",
                "end_use",
                "country_code",
                "unit",
                "energy",
                "year",
            ],
        )
        .squeeze()
        .xs("final_energy", level="energy")
    )

    # Map JRC end uses to annual commercial demand
    mapped_end_uses = _map_jrc_to_eurostat(
        energy_balance, jrc_end_use_df, fill_missing_values, country_codes
    )

    official_commercial_data = []
    if "GBR" in country_codes:
        if not paths_to_ecuk_end_use:
            raise ValueError(
                "GBR is in scope, but no ECUK end-use workbook was provided."
            )
        official_commercial_data.append(
            _map_uk_official_to_eurostat(
                energy_balance,
                paths_to_ecuk_end_use[0],
                paths_to_uk_jrc_idees_2015,
            )
        )

    for official_data in official_commercial_data:
        mapped_end_uses = _drop_matching_country_end_uses(
            mapped_end_uses, official_data
        )

    # Add official data, Swiss data when in scope, and ambient heat from heat pumps.
    mapped_end_use_parts = [mapped_end_uses]
    mapped_end_use_parts.extend(official_commercial_data)
    if "CHE" in country_codes:
        # 'fuel' is just generic non-electric energy, which we distribute based on
        # household data
        ch_con_fuel = _read_ch_non_hh_non_electricity_demand(
            path_to_ch_end_use, "Tabelle 25", annual_final_energy_demand
        )
        ch_con_elec = _read_ch_non_hh_electricity_demand(path_to_ch_end_use, "Tabelle26")
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
        path_to_ecuk_end_use,
        paths_to_uk_jrc_idees_2015,
        energy_balance.columns,
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

    ecuk_years = [year for year in target_years if year >= 2020]
    bridge_years = [year for year in target_years if 2016 <= year <= 2019]
    legacy_years = [year for year in target_years if year <= 2015]

    pieces = []
    ecuk_endpoint = read_ecuk_service_end_use_shares(path_to_ecuk_end_use, [2020]).xs(
        2020, level="year"
    )
    if ecuk_years:
        pieces.append(read_ecuk_service_end_use_shares(path_to_ecuk_end_use, ecuk_years))

    if legacy_years or bridge_years:
        if not paths_to_uk_jrc_idees_2015:
            raise ValueError(
                "GBR annual demand before 2020 requires the legacy UK "
                "JRC-IDEES 2015 workbook."
            )
        legacy_shares = _read_uk_jrc_2015_end_use_shares(
            paths_to_uk_jrc_idees_2015[0]
        )
        if legacy_years:
            pieces.append(
                _select_legacy_uk_jrc_shares_for_years(legacy_shares, legacy_years)
            )
        if bridge_years:
            legacy_endpoint = legacy_shares.xs(2015, level="year")
            pieces.append(
                _interpolate_uk_jrc_ecuk_shares(
                    legacy_endpoint, ecuk_endpoint, bridge_years
                )
            )

    if not pieces:
        raise ValueError("Could not derive official UK commercial end-use shares.")

    return pd.concat(pieces).sort_index()


def _read_uk_jrc_2015_end_use_shares(path_to_uk_jrc_idees_2015: str) -> pd.Series:
    jrc_end_use_df = read_jrc_heat_tertiary_sector_data(
        [path_to_uk_jrc_idees_2015]
    ).xs("final_energy", level="energy")
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
    pieces = []
    for target_year in target_years:
        if target_year in available_years:
            source_year = target_year
        else:
            source_year = min(
                available_years,
                key=lambda year: (abs(year - target_year), -year),
            )
        piece = legacy_shares.xs(source_year, level="year", drop_level=False)
        piece = piece.reset_index("year", drop=True)
        piece = piece.to_frame("value").assign(year=target_year).set_index(
            "year", append=True
        )["value"]
        pieces.append(piece.reorder_levels(legacy_shares.index.names))
    return pd.concat(pieces)


def _interpolate_uk_jrc_ecuk_shares(
    legacy_2015: pd.Series, ecuk_2020: pd.Series, target_years: list[int]
) -> pd.Series:
    legacy_2015 = legacy_2015.reorder_levels(
        ["carrier_name", "end_use", "country_code"]
    ).sort_index()
    ecuk_2020 = ecuk_2020.reorder_levels(
        ["carrier_name", "end_use", "country_code"]
    ).sort_index()
    index = legacy_2015.index.union(ecuk_2020.index)
    legacy_2015 = legacy_2015.reindex(index, fill_value=0)
    ecuk_2020 = ecuk_2020.reindex(index, fill_value=0)

    pieces = []
    for target_year in target_years:
        weight = (target_year - 2015) / (2020 - 2015)
        shares = legacy_2015.mul(1 - weight).add(ecuk_2020.mul(weight))
        shares = _normalise_end_use_shares_by_carrier(shares)
        shares = shares.to_frame("value").assign(year=target_year).set_index(
            "year", append=True
        )["value"]
        pieces.append(shares)

    return pd.concat(pieces).reorder_levels(
        ["carrier_name", "end_use", "country_code", "year"]
    )


def _normalise_end_use_shares_by_carrier(shares: pd.Series) -> pd.Series:
    denominators = shares.groupby(level=["carrier_name", "country_code"]).transform(
        "sum"
    )
    return shares.div(denominators).where(denominators > 0).dropna()


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
    fill_missing_values: dict[str, list[str]],
    country_codes: list[str],
) -> pd.DataFrame:
    jrc_end_use_percent = _jrc_end_use_percent(jrc_end_use_df)
    jrc_end_use_percent = _fill_missing_countries_and_years(
        jrc_data=jrc_end_use_percent,
        fill_missing_values=fill_missing_values,
        country_codes=country_codes,
    )
    jrc_end_use_percent = _extend_jrc_share_years(
        jrc_end_use_percent, energy_balance.columns
    )

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


def _extend_jrc_share_years(
    jrc_end_use_percent: pd.DataFrame, target_years: pd.Index
) -> pd.DataFrame:
    """Copy the latest available JRC end-use shares to missing target years."""
    jrc_end_use_percent = jrc_end_use_percent.unstack("year")
    available_years = sorted(jrc_end_use_percent.columns)
    if not available_years:
        return jrc_end_use_percent.stack()

    for target_year in target_years:
        if target_year in jrc_end_use_percent.columns:
            continue
        fallback_years = [year for year in available_years if year <= target_year]
        fallback_year = fallback_years[-1] if fallback_years else available_years[-1]
        jrc_end_use_percent[target_year] = jrc_end_use_percent[fallback_year]

    return jrc_end_use_percent.sort_index(axis=1).stack()


def _fill_missing_countries_and_years(
    jrc_data: pd.DataFrame,
    fill_missing_values: dict[str, str],
    country_codes: list[str],
) -> pd.DataFrame:
    # Only fill countries requested by the current shape set. This avoids requiring
    # unrelated reference countries for other configured fallback cases.
    fill_missing_values = {
        country: neighbors
        for country, neighbors in fill_missing_values.items()
        if country in country_codes and country != "CHE"
    }
    jrc_data = jrc_data.unstack("country_code")
    if isinstance(jrc_data.columns, pd.MultiIndex):
        if "value" not in jrc_data.columns.get_level_values(0):
            raise ValueError(
                "Unexpected JRC data columns after unstacking country_code: "
                f"{jrc_data.columns.tolist()}"
            )
        jrc_data = jrc_data.loc[:, "value"]

    for country, neighbors in fill_missing_values.items():
        available_neighbors = [
            neighbor for neighbor in neighbors if neighbor in jrc_data.columns
        ]
        if available_neighbors:
            jrc_data = jrc_data.assign(
                **{country: jrc_data[available_neighbors].mean(axis=1)}
            )

    jrc_data = jrc_data.stack().unstack("year")
    jrc_data.columns = jrc_data.columns.astype(int)
    return jrc_data.stack()


def _read_ch_non_hh_electricity_demand(
    path_to_ch_end_use: str, sheet_name: str
) -> pd.DataFrame:
    return (
        _get_ch_sheet(
            path_to_ch_end_use,
            sheet_name,
            skipfooter=4,
            translation=CH_HH_END_USE_TRANSLATION,
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
        path_to_ch_end_use,
        sheet_name,
        skipfooter=4,
        translation=CH_HH_END_USE_TRANSLATION,
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
        ch_con_disaggregated.groupby(level="end_use").sum().reindex_like(ch_con),
        ch_con,
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


def _get_ch_sheet(
    path_to_excel: str, sheet: str, skipfooter, translation=None
) -> pd.DataFrame:
    df = pd.read_excel(
        path_to_excel, sheet_name=sheet, skiprows=9, skipfooter=skipfooter, index_col=1
    ).drop(["Unnamed: 0", "Δ ’00 – ’18"], axis=1, errors="ignore")
    df.index = df.index.str.strip()
    df.columns = df.columns.astype(int)
    df = df.drop(2019, axis=1, errors="ignore")

    if translation is not None:
        return df.groupby(translation).sum()
    else:
        return df


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
        rows.append(
            {
                "index": df.index[row_pos],
                "column": df.columns[col_pos],
            }
        )
    return pd.DataFrame(rows).to_string(index=False)


def _as_list(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    return list(value)


if __name__ == "__main__":
    get_heat_demand(
        path_to_hh_end_use=snakemake.input.hh_end_use,
        path_to_ch_end_use=snakemake.input.ch_end_use,
        path_to_energy_balance=snakemake.input.energy_balance,
        path_to_commercial_demand=snakemake.input.commercial_demand,
        paths_to_ecuk_end_use=_as_list(snakemake.input.ecuk_end_use),
        paths_to_uk_jrc_idees_2015=_as_list(snakemake.input.uk_jrc_idees_2015),
        path_to_carrier_names=snakemake.input.carrier_names,
        heat_tech_params=snakemake.params.heat_tech_params,
        country_codes=snakemake.params.countries,
        model_years=snakemake.params.model_years,
        fill_missing_values=snakemake.params.fill_missing_values,
        path_to_electricity_demand=snakemake.output.electricity,
        path_to_output=snakemake.output.total_demand,
    )
