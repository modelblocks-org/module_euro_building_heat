"""Helpers for standardising UK ECUK final energy demand."""

import _schemas
import numpy as np
import pandas as pd

END_USE_MAPPING = {
    "space heating": "space_heat",
    "water heating": "hot_water",
    "cooking/catering": "cooking",
    "lighting and appliances": "end_use_electricity",
    "computing": "end_use_electricity",
    "cooling and ventilation": "end_use_electricity",
    "lighting": "end_use_electricity",
    "other": "end_use_electricity",
}

CARRIER_MAPPING = {
    "electricity": "electricity",
    "natural gas": "gas",
    "oil": "oil",
    "solid fuel": "solid_fossil",
    "heat": "heat",
    "heat sold": "heat",
    "district heating": "heat",
    "other": "biomass_and_waste",
    "bioenergy & waste": "biomass_and_waste",
    "bioenergy and waste": "biomass_and_waste",
}

SECTOR_TRANSLATION = {"residential": "Domestic", "services": "Services"}

ECUK_KTOE_TO_TWH = 0.01163
BASELINE_COLUMNS = _schemas.BaselineSchema.get_column_names()


def get_sector_demand(path: str, sector: str) -> pd.DataFrame:
    """Read one ECUK sector as a standard final-energy baseline."""
    if sector not in SECTOR_TRANSLATION:
        raise ValueError(
            f"Sector must be one of {sorted(SECTOR_TRANSLATION)}, got {sector!r}."
        )

    table = _read_table_u2(path)
    table["Year"] = pd.to_numeric(table["Year"], errors="coerce")

    table = table.loc[
        table["Sector"].astype(str).str.strip().eq(SECTOR_TRANSLATION[sector])
    ].copy()

    table["source_end_use"] = table["End use"].astype(str).str.strip().str.casefold()

    overall_totals = table.loc[table["source_end_use"].eq("overall total")]

    table["end_use"] = table["source_end_use"].map(END_USE_MAPPING)
    components = table.dropna(subset=["Year", "end_use"])

    carrier_columns = {
        column: CARRIER_MAPPING[str(column).strip().casefold()]
        for column in table.columns
        if str(column).strip().casefold() in CARRIER_MAPPING
    }

    values = components.melt(
        id_vars=["Year", "end_use"],
        value_vars=list(carrier_columns),
        var_name="source_carrier",
        value_name="value",
    )

    values["carrier_name"] = values["source_carrier"].map(carrier_columns)
    values["value"] = pd.to_numeric(values["value"], errors="coerce")

    values = (
        values.dropna(subset=["value"])
        .assign(year=lambda df: df["Year"].astype(int))
        .groupby(["end_use", "carrier_name", "year"], as_index=False)["value"]
        .sum()
    )

    expected = _source_carrier_totals(overall_totals, carrier_columns)
    actual = values.groupby(["year", "carrier_name"])["value"].sum()

    if not np.allclose(actual.reindex(expected.index), expected, rtol=1e-9, atol=1e-9):
        raise RuntimeError(f"ECUK Table U2 checksum failed for {sector}.")

    return (
        values.assign(
            country_code="GBR",
            sector=sector,
            unit="twh",
            energy="final_energy",
            value=lambda df: df["value"] * ECUK_KTOE_TO_TWH,
        )[BASELINE_COLUMNS]
        .sort_values(["year", "end_use", "carrier_name"])
        .reset_index(drop=True)
    )


def _read_table_u2(path: str) -> pd.DataFrame:
    raw = pd.read_excel(path, sheet_name="Table U2", header=None)

    header_row = next(
        row_number
        for row_number, row in raw.iterrows()
        if {"Year", "Sector", "End use"}.issubset(row.astype(str).str.strip())
    )

    table = raw.iloc[header_row + 1 :].copy()
    table.columns = raw.iloc[header_row].astype(str).str.strip()

    return table.dropna(how="all")


def _source_carrier_totals(
    totals: pd.DataFrame, carrier_columns: dict[object, str]
) -> pd.Series:
    result = totals.melt(
        id_vars="Year",
        value_vars=list(carrier_columns),
        var_name="source_carrier",
        value_name="value",
    )

    result["carrier_name"] = result["source_carrier"].map(carrier_columns)
    result["value"] = pd.to_numeric(result["value"], errors="coerce")
    result["year"] = pd.to_numeric(result["Year"], errors="coerce")

    return (
        result.dropna(subset=["year", "value"])
        .assign(year=lambda df: df["year"].astype(int))
        .groupby(["year", "carrier_name"])["value"]
        .sum()
        .sort_index()
    )
