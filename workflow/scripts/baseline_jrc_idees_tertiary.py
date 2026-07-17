"""Process JRC-IDEES tertiary sector heat demand data."""

import sys
from typing import TYPE_CHECKING, Any

import _jrc
import _plots
import _schemas
import numpy as np
import pandas as pd

if TYPE_CHECKING:
    snakemake: Any


def _clean_df(df: pd.DataFrame, energy_type: str):
    country_code = df.index.names[0].split(" - ")[0]
    df = _select_year_columns(df)
    year_columns = df.columns
    df = df.assign(end_use=pd.NA)
    df["end_use"] = df["end_use"].astype("object")
    end_use_rows = df.index.isin(_jrc.TERTIARY_END_USES.keys())
    df.loc[end_use_rows, "end_use"] = df.index[end_use_rows]
    df.end_use = df.end_use.fillna(df.end_use.ffill())

    df = (
        df.dropna(how="all", subset=year_columns)
        .set_index("end_use", append=True)
        .drop(_jrc.TERTIARY_END_USES.keys(), level=0)
        .groupby([_jrc.TERTIARY_CARRIERS, _jrc.TERTIARY_END_USES], level=[0, 1])
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


def process_country_tertiary_data(filepath: str, energy_type: str) -> pd.DataFrame:
    """Process values for a single country."""
    df = pd.read_excel(
        filepath, sheet_name=_jrc.TERTIARY_ENERGY_SHEET[energy_type], index_col=0
    )
    df = _clean_df(df, energy_type)

    # Add electricity demand that is direct to end-use (TVs, lights, etc).
    df_summary = pd.read_excel(filepath, sheet_name="SER_summary", index_col=0)
    df_summary = _select_year_columns(df_summary)
    df_elec = _summary_row(
        df_summary,
        row_label="Specific electricity uses",
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
            _summary_row(
                df_summary,
                row_label="Energy consumption by fuel - Eurostat structure (ktoe)",
            ),
        )
    return df


def get_tertiary_data(filepaths: list[str], energy_type: str) -> pd.DataFrame:
    """Read JRC-IDEES tertiary data and return the processed long dataframe."""
    dfs = []
    for file in filepaths:
        df = process_country_tertiary_data(file, energy_type)
        dfs.append(df)
    if not dfs:
        raise RuntimeError(f"Tertiary data parsing was empty: {energy_type!r}")

    return pd.concat(dfs).stack().rename("value").reset_index()


def main() -> None:
    """Main snakemake process."""
    country_files = list(snakemake.input.latest_jrc)
    country_files.append(snakemake.input.uk_2015)

    final_df = get_tertiary_data(country_files, "final_energy")
    useful_df = get_tertiary_data(country_files, "useful_energy")

    countries = snakemake.params.countries
    final_df = _schemas.TertiaryJRCSchema.validate_countries(final_df, countries)
    final_df.to_csv(snakemake.output.final)
    useful_df = _schemas.TertiaryJRCSchema.validate_countries(useful_df, countries)
    useful_df.to_csv(snakemake.output.useful)

    fig, axes = _plots.plot_bar_histogram(
        final_df, "end_use", container_col="country_code", format_container=False
    )
    _plots.plot_value_histogram(
        useful_df,
        container_col="country_code",
        label="useful_energy",
        fig=fig,
        axes=axes,
        unit="ktoe",
    )
    fig.savefig(snakemake.output.plot, bbox_inches="tight")


if __name__ == "__main__":
    sys.stderr = open(snakemake.log[0], "w", buffering=1)
    main()
