"""Process CHE heat final demand data."""

import sys
from typing import TYPE_CHECKING, Any

import _plots
import numpy as np
import pandas as pd

if TYPE_CHECKING:
    snakemake: Any

PJ_TO_TWH = 1 / 3.6

# FIXME: these should be standard across all baseline sources
CH_ENERGY_CARRIER_TRANSLATION = {
    "Heizöl": "oil",
    "Erdgas": "gas",
    "El. Widerstandsheizungen": "direct_electric",
    "El. Wärmepumpen ¹⁾": "heat_pump",
    "El. Ohm'sche Anlagen": "direct_electric",
    "El. Wärmepumpen": "heat_pump",
    "Elektrizität": "electricity",
    "Holz": "biofuel",
    "Kohle": "solid_fossil",
    "Fernwärme": "heat",
    "Umweltwärme": "ambient_heat",
    "Solar": "solar_thermal",
}
NON_HEAT_ELECTRICAL_USES = [
    # "Raumwärme",
    # "Warmwasser",
    "Klima, Lüftung, HT",
    "I&K, inklusive Unterhaltung",
    # "Kochherde",
    "Beleuchtung",
    "Antriebe, Prozesse",
    "sonstige Elektrogeräte",
]
CH_HH_END_USE_TRANSLATION = {
    "Raumwärme": "space_heat",
    "Warmwasser": "hot_water",
    "Prozesswärme": "cooking",
    "Beleuchtung": "end_use_electricity",
    "Klima, Lüftung, HT": "end_use_electricity",
    "I&K, Unterhaltung": "end_use_electricity",
    "Antriebe, Prozesse": "end_use_electricity",
    "sonstige": "end_use_electricity",
}
SUPPORTED_YEARS = [2000, 2024]  # Right and left inclusive


def _ch_sheet_year_columns(raw: pd.DataFrame) -> tuple[int, list[int], list[int]]:
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
    raise ValueError("Could not find year columns.")


def parse_sheet(path_to_excel: str, sheet: str) -> pd.DataFrame:
    raw = pd.read_excel(path_to_excel, sheet_name=sheet, header=None)
    header_row, year_columns, years = _ch_sheet_year_columns(raw)
    label_column = min(year_columns) - 1
    if label_column < 0:
        raise ValueError(f"Could not find row labels in Swiss sheet {sheet}.")

    df = (
        raw.iloc[header_row + 1 :, [label_column, *year_columns]]
        .dropna(how="all")
        .set_index(label_column)
    )
    df.index = df.index.astype(str).str.strip()
    df = df.loc[df.index != ""]
    df.columns = pd.Index(years, name="year")

    expected_years = set(range(SUPPORTED_YEARS[0], SUPPORTED_YEARS[-1] + 1))
    missing_years = set(years) ^ expected_years
    if missing_years:
        raise ValueError(
            f"Swiss end-use sheet {sheet!r} parsing missed years: {missing_years}."
        )
    df = df.apply(pd.to_numeric, errors="coerce").dropna(how="all")
    return df.mul(PJ_TO_TWH)


def translate_sheet(
    path_to_excel: str, sheet: str, translator: dict[str, str]
) -> pd.DataFrame:
    """Run parsing on an Excel sheet and and convert it to our internal schema."""
    df = parse_sheet(path_to_excel, sheet)
    expected_total = df.loc["Total"].sum()

    result = df.groupby(translator).sum()
    if not np.isclose(result.to_numpy().sum(), expected_total):
        raise RuntimeError(f"Parsing for {sheet!r} had a checksum failure.")
    return result


def get_residential_demand(path_to_ch_end_use: str) -> pd.DataFrame:
    """Get Swiss residential end-use demand in TWh."""
    sheets = {
        "space_heat": "Tabelle20",
        "hot_water": "Tabelle22",
        "cooking": "Tabelle23",
    }
    metadata = {
        "country_code": "CHE",
        "sector": "residential",
        "unit": "twh",
        "energy": "final_energy",
    }

    heating_df = (
        pd.concat(
            {
                end_use: translate_sheet(
                    path_to_ch_end_use, sheet, translator=CH_ENERGY_CARRIER_TRANSLATION
                )
                for end_use, sheet in sheets.items()
            },
            names=["end_use", "carrier_name"],
        )
        .rename(columns=int)
        .rename_axis(columns="year")
        .assign(**metadata)
        .set_index(list(metadata), append=True)
        .stack()
        .rename("value")
        .reset_index()
    )

    direct_elec_df = (
        parse_sheet(path_to_ch_end_use, "Tabelle24")
        .loc["Total"]
        .rename_axis("year")
        .rename("value")
        .reset_index()
        .assign(end_use="end_use_electricity", carrier_name="electricity", **metadata)
    )

    res_df = pd.concat([heating_df, direct_elec_df], ignore_index=True)

    totals = parse_sheet(path_to_ch_end_use, "Tabelle17").loc[
        "Total Endenergieverbrauch"
    ]

    if not np.isclose(res_df["value"].sum(), totals.sum()):
        raise RuntimeError("Parsing for residential demand had a checksum failure.")

    return res_df


def _read_ch_non_hh_non_electricity_demand(
    path_to_ch_end_use: str, hh_final_energy_demand: pd.DataFrame
):
    ch_con = translate_sheet(
        path_to_ch_end_use, "Tabelle27", translator=CH_HH_END_USE_TRANSLATION
    )
    # NOTE: non-electric energy in services is not very detailed,
    # so we assume carrier ratios are the same as in households
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


def _read_ch_non_hh_electricity_demand(path_to_ch_end_use: str) -> pd.DataFrame:
    return (
        translate_sheet(
            path_to_ch_end_use, "Tabelle28", translator=CH_HH_END_USE_TRANSLATION
        )
        .assign(carrier_name="electricity", country_code="CHE")
        .set_index(["country_code", "carrier_name"], append=True)
        .stack()
        .rename_axis(index=["end_use", "country_code", "carrier_name", "year"])
        .mul(PJ_TO_TWH)
    )


def get_services_demand(path_to_ch_end_use: str, residential_df: pd.DataFrame):
    fuel_con = _read_ch_non_hh_non_electricity_demand(
        path_to_ch_end_use, residential_df
    )
    elec_con = _read_ch_non_hh_electricity_demand(path_to_ch_end_use)


def main() -> None:
    """Main snakemake process."""
    # sector = snakemake.wildcards.sector
    raw_file = snakemake.input.raw_stats

    residential_df = get_residential_demand(raw_file)
    residential_df.to_csv(snakemake.output.residential)
    fig, _ = _plots.plot_bar_histogram(
        residential_df, "end_use", container_col="country_code", unit="TWh"
    )
    fig.suptitle("Residential final energy demand")
    fig.savefig(snakemake.output.plot, bbox_inches="tight")

    # services_df = get_services_demand(raw_file, residential_df)


if __name__ == "__main__":
    sys.stderr = open(snakemake.log[0], "w", buffering=1)
    main()
