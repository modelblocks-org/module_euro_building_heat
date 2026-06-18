"""Helpers for UK ECUK service-sector end-use data."""

from collections.abc import Iterable

END_USE_TRANSLATION = {
    "heating": "space_heat",
    "hot water": "hot_water",
    "catering": "cooking",
}
DOMESTIC_END_USE_TRANSLATION = {
    "space heating": "space_heat",
    "water heating": "hot_water",
    "cooking/catering": "cooking",
}

CARRIER_TRANSLATION = {
    "electricity": "electricity",
    "natural gas": "gas",
    "oil": "oil",
    "solid fuel": "solid_fossil",
    "heat": "heat",
    "heat sold": "heat",
    "district heating": "heat",
    "other": "biofuel",
    "bioenergy and waste": "biofuel",
}
ECUK_KTOE_TO_TWH = 0.01163


def read_ecuk_service_end_use_shares(path: str, target_years: Iterable[int]):
    """Read ECUK service-sector end-use shares for Great Britain."""
    import pandas as pd

    raw = pd.read_excel(path, sheet_name="Table U5", header=None)
    table = _table_from_raw_ecuk_sheet(raw)
    return ecuk_service_end_use_shares_from_table(table, target_years)


def read_ecuk_domestic_final_demand(path: str):
    """Read ECUK domestic end-use demand for Great Britain in TWh."""
    import pandas as pd

    raw = pd.read_excel(path, sheet_name="Table U2", header=None)
    table = _table_from_raw_ecuk_sheet(
        raw, required_columns=("Year", "Sector", "End use")
    )
    return ecuk_domestic_final_demand_from_table(table)


def read_ecuk_sector_energy_balance(path: str, sector: str):
    """Read ECUK sector fuel totals for Great Britain in TWh."""
    import pandas as pd

    raw = pd.read_excel(path, sheet_name="Table U2", header=None)
    table = _table_from_raw_ecuk_sheet(
        raw, required_columns=("Year", "Sector", "End use")
    )
    return ecuk_sector_energy_balance_from_table(table, sector)


def ecuk_service_end_use_shares_from_table(table, target_years: Iterable[int]):
    """Convert an ECUK Table U5-like table to carrier/end-use shares."""
    import pandas as pd

    target_years = [int(year) for year in target_years]
    table = table.copy()
    table["Year"] = pd.to_numeric(table["Year"], errors="coerce")
    table = table[table["Sub-sector"].astype(str).str.strip().eq("Total")]

    records = []
    for column in table.columns:
        parsed = _parse_end_use_fuel_column(column)
        if parsed is None:
            continue
        end_use_label, end_use, carrier = parsed
        values = pd.to_numeric(table[column], errors="coerce")
        for year, value in zip(table["Year"], values):
            if pd.isna(year) or pd.isna(value):
                continue
            records.append(
                {
                    "source_year": int(year),
                    "end_use_label": end_use_label,
                    "end_use": end_use,
                    "carrier_name": carrier,
                    "value": float(value),
                }
            )

    if not records:
        raise ValueError("Could not find ECUK Table U5 service end-use data.")

    values = pd.DataFrame.from_records(records)
    available_years = sorted(values["source_year"].unique())
    missing_years = sorted(set(target_years) - set(available_years))
    if missing_years:
        raise ValueError(
            "ECUK Table U5 is missing requested model years: "
            f"{missing_years}. Available years: {available_years}."
        )
    shares = []
    for target_year in target_years:
        source_values = values[values["source_year"].eq(target_year)]
        denominators = source_values.groupby("carrier_name")["value"].sum()
        output_values = source_values.dropna(subset=["end_use"])
        for row in output_values.itertuples(index=False):
            denominator = denominators.loc[row.carrier_name]
            if denominator <= 0:
                continue
            shares.append(
                {
                    "carrier_name": row.carrier_name,
                    "end_use": row.end_use,
                    "country_code": "GBR",
                    "year": target_year,
                    "value": row.value / denominator,
                }
            )

    if not shares:
        raise ValueError("Could not derive ECUK service end-use shares.")

    return (
        pd.DataFrame.from_records(shares)
        .groupby(["carrier_name", "end_use", "country_code", "year"])["value"]
        .sum()
    )


def ecuk_domestic_final_demand_from_table(table):
    """Convert an ECUK Table U2-like table to domestic final demand."""
    records = _ecuk_table_u2_records(table, sector="Domestic", end_use_totals=False)
    return _ecuk_records_to_frame(
        records, index_names=["end_use", "carrier_name", "country_code"]
    )


def ecuk_sector_energy_balance_from_table(table, sector: str):
    """Convert an ECUK Table U2-like table to sector fuel totals."""
    records = _ecuk_table_u2_records(table, sector=sector, end_use_totals=True)
    return _ecuk_records_to_frame(records, index_names=["carrier_name", "country_code"])


def _ecuk_table_u2_records(table, sector: str, end_use_totals: bool):
    import pandas as pd

    table = table.copy()
    table["Year"] = pd.to_numeric(table["Year"], errors="coerce")
    sector_rows = table["Sector"].astype(str).str.strip().eq(sector)
    table = table[sector_rows].copy()
    table["End use"] = table["End use"].astype(str).str.strip()
    if end_use_totals:
        table = table[table["End use"].str.casefold().eq("overall total")]
    else:
        table["end_use"] = (
            table["End use"].str.casefold().map(DOMESTIC_END_USE_TRANSLATION)
        )
        table = table.dropna(subset=["end_use"])

    records = []
    for column in table.columns:
        carrier = CARRIER_TRANSLATION.get(str(column).strip().casefold())
        if carrier is None:
            continue
        values = pd.to_numeric(table[column], errors="coerce")
        for row, value in zip(table.itertuples(index=False), values):
            year = getattr(row, "Year")
            if pd.isna(year) or pd.isna(value):
                continue
            record = {
                "carrier_name": carrier,
                "country_code": "GBR",
                "year": int(year),
                "value": float(value) * ECUK_KTOE_TO_TWH,
            }
            if not end_use_totals:
                record["end_use"] = getattr(row, "end_use")
            records.append(record)

    if not records:
        raise ValueError(f"Could not find ECUK Table U2 data for {sector}.")
    return records


def _ecuk_records_to_frame(records, index_names: list[str]):
    import pandas as pd

    return (
        pd.DataFrame.from_records(records)
        .groupby([*index_names, "year"])["value"]
        .sum()
        .unstack("year")
        .rename_axis("year", axis=1)
    )


def _table_from_raw_ecuk_sheet(raw, required_columns=("Year", "Sub-sector")):
    header_row = None
    for row_number, row in raw.iterrows():
        values = row.astype(str).str.strip().tolist()
        if set(required_columns).issubset(values):
            header_row = row_number
            break

    if header_row is None:
        return _table_from_legacy_ecuk_sheet(raw)

    table = raw.iloc[header_row + 1 :].copy()
    table.columns = raw.iloc[header_row].astype(str).str.strip()
    return table.dropna(how="all")


def _table_from_legacy_ecuk_sheet(raw):
    import pandas as pd

    end_use_row = None
    for row_number, row in raw.iterrows():
        values = row.astype(str).str.strip().str.lower().tolist()
        if "catering" in values and "heating" in values:
            end_use_row = row_number
            break

    if end_use_row is None or end_use_row + 1 not in raw.index:
        raise ValueError("Could not find the ECUK Table U5 header row.")

    end_uses = raw.iloc[end_use_row].ffill()
    fuels = raw.iloc[end_use_row + 1]
    columns = ["Sub-sector"]
    for end_use, fuel in zip(end_uses.iloc[1:], fuels.iloc[1:]):
        if pd.isna(end_use) or pd.isna(fuel):
            columns.append(None)
        else:
            columns.append(f"{str(end_use).strip()} - {str(fuel).strip()}")

    records = []
    current_year = None
    for _, row in raw.iloc[end_use_row + 2 :].iterrows():
        first_value = row.iloc[0]
        year = pd.to_numeric(first_value, errors="coerce")
        if pd.notna(year):
            current_year = int(year)
            continue
        if current_year is None or pd.isna(first_value):
            continue

        record = {"Year": current_year, "Sub-sector": str(first_value).strip()}
        for column, value in zip(columns[1:], row.iloc[1:]):
            if column is not None:
                record[column] = value
        records.append(record)

    if not records:
        raise ValueError("Could not find ECUK Table U5 service rows.")

    return pd.DataFrame.from_records(records)


def _parse_end_use_fuel_column(column):
    if " - " not in str(column):
        return None

    end_use_label, fuel_label = [
        value.strip().lower() for value in str(column).split(" - ", maxsplit=1)
    ]
    if end_use_label == "total" or fuel_label == "all":
        return None

    carrier = CARRIER_TRANSLATION.get(fuel_label)
    if carrier is None:
        return None

    return end_use_label, END_USE_TRANSLATION.get(end_use_label), carrier
