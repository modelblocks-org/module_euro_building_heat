"""Process JRC-IDEES tertiary sector heat demand data."""

import numpy as np
import pandas as pd

idx = pd.IndexSlice
JRC_HEAT_INDEX_NAMES = [
    "carrier_name",
    "end_use",
    "country_code",
    "unit",
    "energy",
    "year",
]

END_USES = {
    "Space heating": "space_heat",
    "Space cooling": "end_use_electricity",
    "Hot water": "hot_water",
    "Catering": "cooking",
}
CARRIER_NAMES = {
    "Advanced electric heating": "electricity",
    "Biomass": "biofuel",
    "Biomass and waste": "biofuel",
    "Biomass and wastes": "biofuel",
    "Conventional electric heating": "electricity",
    "Conventional gas heaters": "gas",
    "Derived heat": "heat",
    "Diesel oil": "oil",
    "Distributed heat": "heat",
    "Electric space cooling": "electricity",
    "Electricity": "electricity",
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


def process_jrc_heat_tertiary_sector_data(
    paths_to_national_data: list[str], out_path: str
):
    """Process JRC-IDEES tertiary data to extract its heat demand."""
    read_jrc_heat_tertiary_sector_data(paths_to_national_data).to_csv(out_path)


def read_jrc_heat_tertiary_sector_data(paths_to_national_data: list[str]) -> pd.Series:
    """Read JRC-IDEES tertiary data and return the processed long series."""
    dfs = []
    for file in paths_to_national_data:
        df_final_energy = pd.read_excel(file, sheet_name="SER_hh_fec", index_col=0)
        df_useful_energy = pd.read_excel(file, sheet_name="SER_hh_tes", index_col=0)
        df_summary = pd.read_excel(file, sheet_name="SER_summary", index_col=0)

        df_final_energy = _clean_df(df_final_energy, "final_energy")
        df_useful_energy = _clean_df(df_useful_energy, "useful_energy")

        df = pd.concat([df_final_energy, df_useful_energy]).sort_index()
        df_summary = _select_year_columns(df_summary)

        df_elec = _summary_row(
            df_summary,
            row_label="Specific electricity uses",
            start_label="Energy consumption by end-uses (ktoe)",
            end_label="Shares of energy consumption in end-uses (in %)",
        )

        electricity_index = idx[
            "electricity", "end_use_electricity", :, :, "final_energy"
        ]
        updated_electricity = df.loc[electricity_index, :].add(df_elec, axis=1)
        df.loc[electricity_index, :] = updated_electricity.to_numpy()

        assert np.allclose(
            df.xs("final_energy", level="energy").sum(),
            _summary_row(
                df_summary,
                row_label="Energy consumption by fuel - Eurostat structure (ktoe)",
            ),
        )

        dfs.append(df)

    if not dfs:
        return _empty_jrc_heat_series()

    return pd.concat(dfs).stack()


def _empty_jrc_heat_series() -> pd.Series:
    index = pd.MultiIndex.from_arrays(
        [[] for _ in JRC_HEAT_INDEX_NAMES], names=JRC_HEAT_INDEX_NAMES
    )
    return pd.Series(index=index, dtype=float)


def _clean_df(df: pd.DataFrame, energy_type: str):
    country_code = df.index.names[0].split(" - ")[0]
    df = _select_year_columns(df)
    year_columns = df.columns
    df = df.assign(end_use=pd.NA)
    df["end_use"] = df["end_use"].astype("object")
    end_use_rows = df.index.isin(END_USES.keys())
    df.loc[end_use_rows, "end_use"] = df.index[end_use_rows]
    df.end_use = df.end_use.fillna(df.end_use.ffill())

    df = (
        df.dropna(how="all", subset=year_columns)
        .set_index("end_use", append=True)
        .drop(END_USES.keys(), level=0)
        .groupby([CARRIER_NAMES, END_USES], level=[0, 1])
        .sum()
        .assign(country_code=country_code, unit="ktoe", energy=energy_type)
        .set_index(["country_code", "unit", "energy"], append=True)
        .rename_axis(
            columns="year",
            index=["carrier_name", "end_use", "country_code", "unit", "energy"],
        )
    )
    return df


def _select_year_columns(df: pd.DataFrame) -> pd.DataFrame:
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


def _summary_row(
    df: pd.DataFrame,
    row_label: str,
    start_label: str | None = None,
    end_label: str | None = None,
) -> pd.Series:
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


if __name__ == "__main__":
    process_jrc_heat_tertiary_sector_data(
        paths_to_national_data=snakemake.input.data, out_path=snakemake.output[0]
    )
