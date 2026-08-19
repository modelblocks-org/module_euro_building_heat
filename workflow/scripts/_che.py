"""Helpers for standardising Swiss final energy demand."""

import _schemas
import numpy as np
import pandas as pd

PJ_TO_TWH = 1 / 3.6

CARRIER_MAPPING = {
    "Heizöl": "oil",
    "Erdgas": "gas",
    "El. Widerstandsheizungen": "direct_electric",
    "El. Wärmepumpen ¹⁾": "heat_pump",
    "El. Ohm'sche Anlagen": "direct_electric",
    "El. Wärmepumpen": "heat_pump",
    "Elektrizität": "electricity",
    "Holz": "biomass_and_waste",
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
BASELINE_COLUMNS = _schemas.BaselineSchema.get_column_names()
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
    """Get a wide dataframe from a sheet in the Swiss source workbook."""
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
    """Parse an Excel sheet and translate its row labels."""
    df = parse_sheet(path_to_excel, sheet)
    result = df.groupby(translator).sum()
    # Some published annual totals differ slightly from their displayed components.
    _checksum_totals(
        result.sum(), df.loc[total_label], f"Parsing for {sheet!r}", rtol=CHECKSUM_RTOL
    )
    return result


def get_residential_demand(raw_file_path: str) -> pd.DataFrame:
    """Get Swiss residential final energy demand in TWh."""
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
    result = (
        pd.concat([heat, direct_electricity])
        .assign(**metadata)
        .set_index(list(metadata), append=True)
        .stack()
        .rename("value")
        .reset_index()
    )

    totals = parse_sheet(raw_file_path, "Tabelle17").loc["Total Endenergieverbrauch"]
    _checksum_totals(
        result.groupby("year")["value"].sum(), totals, "Parsing for residential demand"
    )
    return result[BASELINE_COLUMNS]


def _get_services_fuel_demand(
    raw_file_path: str, residential_demand: pd.DataFrame
) -> pd.DataFrame:
    ch_con = translate_sheet(raw_file_path, "Tabelle27", translator=END_USE_MAPPING)
    # Non-electric services demand is not carrier-detailed in the source. Preserve
    # the existing assumption that its carrier ratios match household demand.
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
    raw_file_path: str, residential_demand: pd.DataFrame
) -> pd.DataFrame:
    """Get Swiss service-sector final energy demand in TWh."""
    result = pd.concat(
        [
            _get_services_fuel_demand(raw_file_path, residential_demand),
            _get_services_electric_demand(raw_file_path),
        ],
        ignore_index=True,
    )

    totals = parse_sheet(raw_file_path, "Tabelle26").loc["Total Endenergie"]
    _checksum_totals(
        result.groupby("year")["value"].sum(), totals, "Parsing for services demand"
    )
    return result
