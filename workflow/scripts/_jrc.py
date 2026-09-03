"""Helpers for standardising JRC processing."""

import numpy as np
import pandas as pd

SECTORS = ["RES", "SER"]
KTOE_TO_TWH = 0.01163

CARRIER_MAPPING = {
    "Air conditioning": "electricity",
    "Advanced electric heating": "electricity",
    "Biomass": "biomass_and_waste",
    "Biomass and waste": "biomass_and_waste",
    "Biomass and wastes": "biomass_and_waste",
    "Conventional electric heating": "electricity",
    "Conventional gas heaters": "gas",
    "Derived heat": "heat",
    "Diesel oil": "oil",
    "Distributed heat": "heat",
    "Electric space cooling": "electricity",
    "Electricity": "electricity",
    "Electricity in circulation": "electricity",
    "Electricity in circulation and other use": "electricity",
    "Gas heat pumps": "gas",
    "Gas/Diesel oil incl. biofuels (GDO)": "oil",
    "Gases incl. biogas": "gas",
    "Geothermal energy": "renewable_heat",
    "Geothermal": "renewable_heat",
    "Liquified petroleum gas (LPG)": "oil",
    "Natural gas": "gas",
    "Solar": "renewable_heat",
    "Solids": "solid_fossil",
}
END_USE_MAPPING = {
    "Space heating": "space_heat",
    "Space cooling": "end_use_electricity",
    "Hot water": "hot_water",
    "Catering": "cooking",
    "Water heating": "hot_water",
    "Cooking": "cooking",
}
SHEETS = {"summary": "summary", "final_energy": "hh_fec", "useful_energy": "hh_tes"}
ELECTRIC_END_USE_ROW = {
    "RES": "Specific electricity uses (appliances and lighting)",
    "SER": "Specific electricity uses",
}


def _get_sheet(sector: str, sheet_type: str):
    if sector not in SECTORS:
        raise ValueError(f"Sector must be one of {SECTORS}, got {sector!r}.")
    return f"{sector}_{SHEETS[sheet_type]}"


def _select_year_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Attempts to find the year columns in JRC-IDEES sheets."""
    year_columns = []
    years = []
    for column in df.columns:
        try:
            year = int(column)
        except (TypeError, ValueError):
            continue
        year_columns.append(column)
        years.append(year)

    if not year_columns:
        raise ValueError("Could not find year columns in JRC-IDEES workbook sheet.")

    df = df.loc[:, year_columns].copy()
    df.columns = pd.Index(years, name="year")
    return df


def _clean_df(df: pd.DataFrame, energy_type: str):
    """Tidy up JRC-IDEES long-form sheets."""
    country_code = df.index.names[0].split(" - ")[0]
    df = _select_year_columns(df)
    year_columns = df.columns
    df = df.assign(end_use=pd.NA)
    df["end_use"] = df["end_use"].astype("object")
    end_use_rows = df.index.isin(END_USE_MAPPING.keys())
    df.loc[end_use_rows, "end_use"] = df.index[end_use_rows]
    df.end_use = df.end_use.fillna(df.end_use.ffill())

    df = (
        df.dropna(how="all", subset=year_columns)
        .set_index("end_use", append=True)
        .drop(END_USE_MAPPING.keys(), level=0, errors="ignore")
        .groupby([CARRIER_MAPPING, END_USE_MAPPING], level=[0, 1])
        .sum()
        .assign(country_code=country_code, unit="ktoe", energy=energy_type)
        .set_index(["country_code", "unit", "energy"], append=True)
        .rename_axis(
            columns="year",
            index=["carrier_name", "end_use", "country_code", "unit", "energy"],
        )
    )
    return df


def _summary_row(
    df: pd.DataFrame,
    row_label: str,
    start_label: str | None = None,
    end_label: str | None = None,
) -> pd.Series:
    """Fetch values from JRC-IDEES summary sheets (XXX_summary)."""
    search_df = df
    labels = pd.Index(df.index.astype(str))

    if start_label is not None:
        start_positions = np.flatnonzero(labels == start_label)
        if len(start_positions) == 0:
            raise KeyError(f"Could not find JRC-IDEES summary row: {start_label}")
        start_position = start_positions[0]
    else:
        start_position = 0

    if end_label is not None:
        end_positions = np.flatnonzero(labels == end_label)
        end_positions = end_positions[end_positions >= start_position]
        if len(end_positions) == 0:
            raise KeyError(f"Could not find JRC-IDEES summary row: {end_label}")
        end_position = end_positions[0]
    else:
        end_position = len(search_df)

    search_df = search_df.iloc[start_position:end_position]
    matching_rows = search_df.loc[search_df.index.astype(str) == row_label]
    if matching_rows.empty:
        raise KeyError(f"Could not find JRC-IDEES summary row: {row_label}")

    return matching_rows.iloc[0].astype(float).rename_axis(index="year")


def _process_country_dataset(
    filepath: str, sector: str, energy_type: str
) -> pd.DataFrame:
    """Process values for a single country."""
    df = pd.read_excel(
        filepath, sheet_name=_get_sheet(sector, energy_type), index_col=0
    )
    df = _clean_df(df, energy_type)

    # Add electricity demand that is direct to end-use (TVs, lights, etc).
    df_summary = pd.read_excel(
        filepath, sheet_name=_get_sheet(sector, "summary"), index_col=0
    )
    df_summary = _select_year_columns(df_summary)
    df_elec = _summary_row(
        df_summary,
        row_label=ELECTRIC_END_USE_ROW[sector],
        start_label="Energy consumption by end-uses (ktoe)",
        end_label="Shares of energy consumption in end-uses (in %)",
    )
    electricity_index = pd.IndexSlice[
        "electricity", "end_use_electricity", :, :, energy_type
    ]
    updated_electricity = df.loc[electricity_index, :].add(df_elec, axis=1)
    df.loc[electricity_index, :] = updated_electricity.to_numpy()

    if energy_type == "final_energy":
        assert np.allclose(
            df.xs(energy_type, level="energy").sum(),
            _summary_row(df_summary, row_label="Energy consumption by end-uses (ktoe)"),
        )
    return df


def get_sector_data(
    filepaths: list[str], sector: str, energy_type: str
) -> pd.DataFrame:
    """Read JRC-IDEES heating sector data and return the processed long dataframe."""
    dfs = []
    for file in filepaths:
        df = _process_country_dataset(file, sector, energy_type)
        dfs.append(df)
    if not dfs:
        raise RuntimeError(f"Data parsing result was empty for: {sector}-{energy_type}")

    result = pd.concat(dfs).stack().rename("value").reset_index()
    result["value"] *= KTOE_TO_TWH
    result["unit"] = "twh"
    if sector == "RES":
        result["carrier_name"] = result["carrier_name"].replace(
            {"renewable_heat": "solar_thermal"}
        )
    return result
