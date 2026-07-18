"""Process CHE heat final demand data."""

import sys
from typing import TYPE_CHECKING, Any

import _plots
import numpy as np
import pandas as pd
from _schemas import BaselineSchema

if TYPE_CHECKING:
    snakemake: Any

PJ_TO_TWH = 1 / 3.6

# FIXME: these should be standard across all baseline sources
CARRIER_MAPPING = {
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
END_USE_MAPPING = {
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
CHECKSUM_RTOL = 5e-5
ELECTRIC_HEATING_CARRIERS = {"electricity", "direct_electric", "heat_pump"}
BASELINE_COLUMNS = BaselineSchema.get_column_names()
COMMON_METADATA = {"country_code": "CHE", "unit": "twh", "energy": "final_energy"}


def _ch_sheet_year_columns(raw: pd.DataFrame) -> tuple[int, list[int], list[int]]:
    for row_number, row in raw.iterrows():
        years = pd.to_numeric(row, errors="coerce").dropna().astype(int)
        years = years.loc[years.between(1900, 2100)]
        if not years.empty:
            return row_number, years.index.to_list(), years.to_list()
    raise ValueError("Could not find year columns.")


def _checksum_totals(
    actual: pd.Series, expected: pd.Series, label: str, rtol: float = 1e-5
) -> None:
    """Check calculated values against source totals."""
    if not np.allclose(actual.reindex(expected.index), expected, rtol=rtol):
        raise RuntimeError(f"{label} had a checksum failure.")


def _to_baseline(df: pd.DataFrame, sector: str, **fixed_columns: str) -> pd.DataFrame:
    """Convert a wide dataframe to the standard baseline format."""
    return (
        df.stack()
        .rename("value")
        .reset_index()
        .assign(**fixed_columns, sector=sector, **COMMON_METADATA)[BASELINE_COLUMNS]
    )


def parse_sheet(path_to_excel: str, sheet: str) -> pd.DataFrame:
    """Get a long formatted dataframe from a sheet in the CHE file."""
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
    path_to_excel: str,
    sheet: str,
    translator: dict[str, str],
    total_label: str = "Total",
) -> pd.DataFrame:
    """Run parsing on an Excel sheet and and convert it to our internal schema."""
    df = parse_sheet(path_to_excel, sheet)
    result = df.groupby(translator).sum()
    # Some published annual totals differ slightly from their displayed components.
    _checksum_totals(
        result.sum(), df.loc[total_label], f"Parsing for {sheet!r}", rtol=CHECKSUM_RTOL
    )
    return result


def get_residential_demand(raw_file_path: str) -> pd.DataFrame:
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

    heat = pd.concat(
        {
            end_use: translate_sheet(raw_file_path, sheet, translator=CARRIER_MAPPING)
            for end_use, sheet in sheets.items()
        },
        names=["end_use", "carrier_name"],
    )
    direct_electricity = parse_sheet(raw_file_path, "Tabelle24").loc[["Total"]]
    direct_electricity.index = pd.MultiIndex.from_tuples(
        [("end_use_electricity", "electricity")], names=["end_use", "carrier_name"]
    )
    res_df = (
        pd.concat([heat, direct_electricity])
        .assign(**metadata)
        .set_index(list(metadata), append=True)
        .stack()
        .rename("value")
        .reset_index()
    )

    totals = parse_sheet(raw_file_path, "Tabelle17").loc["Total Endenergieverbrauch"]
    _checksum_totals(
        res_df.groupby("year")["value"].sum(), totals, "Parsing for residential demand"
    )

    return BaselineSchema.validate(res_df)


def _get_services_fuel_demand(
    raw_file_path: str, residential_demand: pd.DataFrame
) -> pd.DataFrame:
    ch_con = translate_sheet(raw_file_path, "Tabelle27", translator=END_USE_MAPPING)
    # NOTE: non-electric energy in services is not very detailed,
    # so we assume carrier ratios are the same as in households.
    # Electrical heating is reported separately in Tabelle28.
    hh_con = (
        residential_demand.loc[
            residential_demand["end_use"].isin(ch_con.index)
            & ~residential_demand["carrier_name"].isin(ELECTRIC_HEATING_CARRIERS)
        ]
        .set_index(["end_use", "carrier_name", "year"])["value"]
        .unstack("year", sort=False)
    )
    carrier_ratios = hh_con.div(
        hh_con.groupby(level="end_use", sort=False).transform("sum")
    )
    result = carrier_ratios.mul(ch_con, level="end_use")

    _checksum_totals(
        result.groupby(level="end_use").sum().stack(),
        ch_con.stack(),
        "Service non-electric carrier allocation",
    )
    return _to_baseline(result, "services")


def _get_services_electric_demand(raw_file_path: str) -> pd.DataFrame:
    return _to_baseline(
        translate_sheet(
            raw_file_path,
            "Tabelle28",
            translator=END_USE_MAPPING,
            total_label="Total Elektrizität",
        ).rename_axis(index="end_use"),
        "services",
        carrier_name="electricity",
    )


def get_services_demand(
    raw_file_path: str, residential_df: pd.DataFrame
) -> pd.DataFrame:
    """Get Swiss service-sector end-use demand in TWh."""
    services_df = pd.concat(
        [
            _get_services_fuel_demand(raw_file_path, residential_df),
            _get_services_electric_demand(raw_file_path),
        ],
        ignore_index=True,
    )

    totals = parse_sheet(raw_file_path, "Tabelle26").loc["Total Endenergie"]
    _checksum_totals(
        services_df.groupby("year")["value"].sum(),
        totals,
        "Parsing for services demand",
    )
    return BaselineSchema.validate(services_df)


def main() -> None:
    """Main snakemake process."""
    raw_file = snakemake.input.raw_stats

    residential_df = get_residential_demand(raw_file)
    services_df = get_services_demand(raw_file, residential_df)

    for df in [residential_df, services_df]:
        sector = df["sector"].iat[0]
        df.to_csv(snakemake.output[sector], index=False)
        fig, _ = _plots.plot_bar_histogram(
            df, "end_use", container_col="country_code", unit="TWh"
        )
        fig.suptitle(f"{sector.capitalize()} final energy demand")
        fig.savefig(snakemake.output[f"{sector}_plot"], bbox_inches="tight")


if __name__ == "__main__":
    sys.stderr = open(snakemake.log[0], "w", buffering=1)
    main()
