"""Prepare annual energy balance data for heat demand calculations."""

import sys
import warnings
from enum import Enum
from string import digits

import _utils
import pandas as pd

GWH_TO_TJ = 3.6
TJ_TO_TWH = 1 / 3600
TWH_TO_TJ = 3600

warnings.filterwarnings(
    "ignore",
    message="Print area cannot be set to Defined name:.*",
    category=UserWarning,
    module="openpyxl.reader.workbook",
)


class CAT_CODE(Enum):
    """Eurostat codes."""

    FINAL_CONSUMPTION_HOUSEHOLD_CATEGORY = "FC_OTH_HH_E"
    FINAL_CONSUMPTION_INDUSTRY_CATEGORY = "FC_IND_E"
    FINAL_CONSUMPTION_OTHER_SECTORS_COMMERCIAL_PUBLIC_SERVICES = "FC_OTH_CP_E"


def generate_annual_energy_balance_nc(
    path_to_energy_balance: str,
    path_to_cat_names: str,
    path_to_carrier_names: str,
    path_to_ch_excel: str,
    path_to_ch_industry_excel: str,
    paths_to_gbr_baselines: list[str],
    path_to_result: str,
    first_year: int,
) -> None:
    """Open a TSV file and reprocess it into a xarray dataset.

    Final dataset will include long names for Eurostat codes.
    Switzerland is not included in Eurostat, and post-Brexit UK coverage is
    incomplete, so national statistics are spliced in for both countries.
    """
    # Names for each consumption category/sub-category and carriers prepared by hand
    cat_names = pd.read_csv(path_to_cat_names, header=0, index_col=0)
    carrier_names = pd.read_csv(path_to_carrier_names, header=0, index_col=0)

    df = _read_eurostat_tsv(
        path_to_energy_balance,
        index_names=["cat_code", "carrier_code", "unit", "country"],
    )
    not_countries = [c for c in df.reset_index().country.unique() if len(c) > 2] + [
        "XK"
    ]
    df = (
        df.drop(axis=0, level="country", labels=not_countries)
        .reset_index(level="country")
        .assign(country=lambda df: df.country.map(_utils.eurostat_to_alpha3))
        .set_index("country", append=True)
    )
    keep_rows = (
        df.index.isin(cat_names.index, level="cat_code")
        & df.index.isin(carrier_names.index, level="carrier_code")
        & df.index.isin(["TJ"], level="unit")
    )
    df = _eurostat_values_to_numeric(df.loc[keep_rows, :]).dropna(how="all")
    df = df.sort_index(axis=1).loc[:, first_year:]

    tdf = df.stack()

    # Add CH energy use.
    # Only covers a subset of sectors and carriers, but should be enough
    ch_energy_use_tdf = _add_ch_energy_balance(
        path_to_ch_excel, path_to_ch_industry_excel, index_levels=tdf.index.names
    )
    tdf = pd.concat([tdf, ch_energy_use_tdf]).sort_index(axis=0)

    # Replace overlapping UK household and service data with ECUK totals.
    tdf = _overlay_gbr_energy_balance(
        tdf, paths_to_gbr_baselines, carrier_names, index_levels=tdf.index.names
    )

    # TODO treat missing values if necessary

    result = tdf.mul(TJ_TO_TWH).rename("value").reset_index()
    result["unit"] = "twh"
    result.set_index(["cat_code", "carrier_code", "unit", "country", "year"])[
        "value"
    ].to_csv(path_to_result)


def _overlay_gbr_energy_balance(
    energy_balance: pd.Series,
    baseline_paths: list[str],
    carrier_names: pd.DataFrame,
    index_levels: list[str],
) -> pd.Series:
    """Replace GBR household and service totals with standardized ECUK data."""
    sector_metadata = {
        "residential": ("FC_OTH_HH_E", "residential_carrier_name"),
        "services": ("FC_OTH_CP_E", "services_carrier_name"),
    }
    additions = []
    for path in baseline_paths:
        baseline = pd.read_csv(path)
        sector = baseline["sector"].iat[0]
        cat_code, carrier_column = sector_metadata[sector]
        carrier_codes = (
            carrier_names[carrier_column]
            .dropna()
            .reset_index()
            .drop_duplicates(carrier_column)
            .set_index(carrier_column)["carrier_code"]
        )
        baseline["carrier_code"] = baseline["carrier_name"].map(carrier_codes)
        if baseline["carrier_code"].isna().any():
            missing = sorted(
                baseline.loc[baseline["carrier_code"].isna(), "carrier_name"].unique()
            )
            raise ValueError(
                f"Missing energy-balance carrier codes for ECUK: {missing}."
            )

        additions.append(
            baseline.assign(
                cat_code=cat_code,
                unit="TJ",
                country="GBR",
                value=lambda df: df["value"] * TWH_TO_TJ,
            )
            .groupby(index_levels, sort=False)["value"]
            .sum()
        )

    official = pd.concat(additions).sort_index()
    official_country_category_years = set(
        zip(
            official.index.get_level_values("country"),
            official.index.get_level_values("cat_code"),
            official.index.get_level_values("year"),
        )
    )
    existing_country_category_years = zip(
        energy_balance.index.get_level_values("country"),
        energy_balance.index.get_level_values("cat_code"),
        energy_balance.index.get_level_values("year"),
    )
    keep = [
        country_category_year not in official_country_category_years
        for country_category_year in existing_country_category_years
    ]
    return pd.concat([energy_balance.loc[keep], official]).sort_index()


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


def _add_ch_energy_balance(path_to_ch_excel, path_to_ch_industry_excel, index_levels):
    household_sheet = "T17a"
    industry_sheet = "T17b"
    other_sectors_sheet = "T17c"

    ch_hh_energy_use = _get_ch_energy_balance_sheet(
        path_to_ch_excel,
        household_sheet,
        skipfooter=9,
        cat_code=CAT_CODE.FINAL_CONSUMPTION_HOUSEHOLD_CATEGORY.value,
    )
    ch_ind_energy_use = _get_ch_energy_balance_sheet(
        path_to_ch_excel,
        industry_sheet,
        skipfooter=12,
        cat_code=CAT_CODE.FINAL_CONSUMPTION_INDUSTRY_CATEGORY.value,
    )
    ch_ser_energy_use = _get_ch_energy_balance_sheet(
        path_to_ch_excel,
        other_sectors_sheet,
        skipfooter=12,
        cat_code=CAT_CODE.FINAL_CONSUMPTION_OTHER_SECTORS_COMMERCIAL_PUBLIC_SERVICES.value,
    )

    ch_waste_energy_use = get_ch_waste_consumption(path_to_ch_excel)
    ch_industry_subsector_energy_use = _get_ch_industry_energy_balance(
        path_to_ch_industry_excel
    )
    ch_transport_energy_use = _get_ch_transport_energy_balance(path_to_ch_excel)

    ch_energy_use_tdf = pd.concat(
        [
            df.reset_index("year")
            .assign(country="CHE", unit="TJ")
            .set_index(["year", "country", "unit"], append=True)
            .squeeze()
            .reorder_levels(index_levels)
            for df in [
                ch_hh_energy_use,
                ch_ind_energy_use,
                ch_ser_energy_use,
                ch_waste_energy_use,
                ch_industry_subsector_energy_use,
                ch_transport_energy_use,
            ]
        ]
    )

    return ch_energy_use_tdf


def _get_ch_energy_balance_sheet(path_to_excel, sheet, skipfooter, cat_code):
    ch_energy_carriers = {
        "Erdölprodukte": "O4000XBIO",
        "Elektrizität": "E7000",
        "Gas": "G3000",
        "Kohle": "C0000X0350-0370",
        "Holzenergie": "R5110-5150_W6000RI",
        "Fernwärme": "H8000",
        "Industrieabfälle": "W6100_6220",
        "Übrige erneuerbare Energien": "RA000",
        "Total\n= %": "TOTAL",
    }
    # Footnote labels lead to some strings randomly ending in numbers; remove them
    remove_digits = str.maketrans("", "", digits)
    df = (
        pd.read_excel(
            path_to_excel,
            skiprows=6,
            skipfooter=skipfooter,
            index_col=0,
            sheet_name=sheet,
            dtype="float",
            na_values=["-"],
            header=[0, 1, 2, 3, 4],
        )
        .xs("TJ", level=-1, axis=1)  # Ignore columns giving % use
        .drop(
            columns=["Erdölprodukte", "Erdölprodukte1"], level=0, errors="ignore"
        )  # ignore the column giving subset of oil use which is light oil
        .rename_axis(index="year")
    )
    df.columns = (
        df.columns.get_level_values(0)
        .str.translate(remove_digits)
        .map(ch_energy_carriers)
        .rename("carrier_code")
    )

    return df.assign(cat_code=cat_code).set_index("cat_code", append=True).stack()


def get_ch_waste_consumption(path_to_excel):
    """Get data on waste burned in WtE plants.

    Present in a sheet CH GEST dataset.
    FIXME: why do the assumption below?
    ASSUME: Small quantity (~2-3%) of fossil fuels consumed in Swiss WtE plants can be
    ignored.
    """
    category_code = "TI_EHG_E"
    carrier_code = "W6100_6220"
    sheet_name = "T27"

    waste_stream_gwh = pd.read_excel(
        path_to_excel,
        sheet_name=sheet_name,
        skiprows=5,
        index_col=0,
        header=[0, 1],
        skipfooter=8,
    )[("Consommation d'énergie (GWh)", "Ordures")]
    waste_stream_tj = waste_stream_gwh * GWH_TO_TJ
    waste_stream_tdf = (
        waste_stream_tj.to_frame(carrier_code)  # carrier code
        .rename_axis(index="year", columns="carrier_code")
        .assign(cat_code=category_code)  # cat code
        .set_index("cat_code", append=True)
        .stack()
    )
    return waste_stream_tdf


def _get_ch_transport_energy_balance(path_to_excel):
    carriers = {
        "Gas übriger Vekehr": ("G3000", "FC_TRA_ROAD_E"),
        "Übrige erneuerbare Energien": ("R5220B", "FC_TRA_ROAD_E"),
        "davon Benzin": ("O4652XR5210B", "FC_TRA_ROAD_E"),
        "davon Diesel": ("O4671XR5220B", "FC_TRA_ROAD_E"),
        "davon Flugtreibstoffe": ("O4000XBIO", "INTAVI"),
        "davon Bahnen": ("E7000", "FC_TRA_RAIL_E"),
        "davon Strasse": ("E7000", "FC_TRA_ROAD_E"),
    }
    # ASSUME "davon Non-Road" is not included in electrified transport

    df = pd.read_excel(
        path_to_excel,
        skiprows=6,
        skipfooter=12,
        index_col=0,
        sheet_name="T17e",
        dtype="float",
        na_values=["-"],
        header=[0, 1, 2, 3, 4],
    ).xs("TJ", level=-1, axis=1)

    # Footnote labels lead to some strings randomly ending in numbers; remove them
    remove_digits = str.maketrans("", "", digits)
    # carrier names span across two column levels, which we merge with fillna

    def carrier_name_func(index):
        return (
            df.columns.to_frame()
            .iloc[:, index]
            .str.translate(remove_digits)
            .map(carriers)
        )

    df.columns = carrier_name_func(0).fillna(carrier_name_func(1)).values

    df = (
        df.T.groupby(level=0)
        .sum()
        .T.rename_axis(index="year", columns="carrier_code")
        .T
    )
    df.index = pd.MultiIndex.from_tuples(df.index, names=("carrier_code", "cat_code"))
    return df.stack()


def _get_ch_industry_energy_balance(path_to_excel):
    ch_subsector_codes = {
        "1": "FC_IND_FBT_E",  # 'Food, beverages & tobacco',
        "2": "FC_IND_TL_E",  # 'Textile & leather',
        "3": "FC_IND_PPP_E",  # 'Paper, pulp & printing',
        "4": "FC_IND_CPC_E",  # 'Chemical & petrochemical',
        "5": "FC_IND_NMM_E",  # 'Non-metallic minerals',
        "6": "FC_IND_NMM_E",  # 'Non-metallic minerals',
        "7": "FC_IND_IS_E",  # 'Iron & steel',
        "8": "FC_IND_NFM_E",  # 'Non-ferrous metals',
        "9": "FC_IND_MAC_E",  # 'Machinery',
        "10": "FC_IND_MAC_E",  # 'Machinery',
        "11": "FC_IND_NSP_E",  # 'Not elsewhere specified (industry)',
        "12": "FC_IND_CON_E",  # 'Construction',
    }
    ch_carrier_sheets = {
        "Elektrizität": "E7000",
        "Erdgas": "G3000",
        "Heizöl extra-leicht": "O4000XBIO",
        "Heizöl mittel und schwer": "O4000XBIO",
        "Industrieabfälle": "W6100_6220",
        "Kohle": "C0000X0350-0370",
        "Fernwärme (Bezug)": "H8000",
        "Holz": "R5110-5150_W6000RI",
    }

    excel = pd.ExcelFile(path_to_excel)
    missing_sheets = sorted(set(ch_carrier_sheets).difference(excel.sheet_names))
    if missing_sheets:
        raise ValueError(f"Missing Swiss industry energy sheets: {missing_sheets}")

    dfs = []
    for sheet_name, carrier_code in ch_carrier_sheets.items():
        df = pd.read_excel(excel, sheet_name=sheet_name, skiprows=3, nrows=22, header=0)
        df = df[df["BranchenNr."].astype(str).isin(ch_subsector_codes)]
        df = df.rename(columns={"BranchenNr.": "cat_code"})
        df["cat_code"] = df["cat_code"].astype(str).map(ch_subsector_codes)
        df = (
            df.drop(columns=["Branchenname", "Sektor"], errors="ignore")
            .set_index("cat_code")
            .rename(
                columns=lambda col: str(int(col)) if isinstance(col, float) else col
            )
        )
        df.columns = df.columns.astype(int).rename("year")
        dfs.append(
            df.T.assign(carrier_code=carrier_code)
            .set_index("carrier_code", append=True)
            .reorder_levels(["carrier_code", "year"])
        )

    return (
        pd.concat(dfs)
        .groupby(level=["carrier_code", "year"])
        .sum()
        .stack()
        .rename("value")
    )


if __name__ == "__main__":
    sys.stderr = open(snakemake.log[0], "w", buffering=1)
    generate_annual_energy_balance_nc(
        path_to_energy_balance=snakemake.input.energy_balance,
        path_to_ch_excel=snakemake.input.ch_energy_balance,
        path_to_ch_industry_excel=snakemake.input.ch_industry_energy_balance,
        paths_to_gbr_baselines=[
            snakemake.input.gbr_residential,
            snakemake.input.gbr_services,
        ],
        path_to_cat_names=snakemake.input.cat_names,
        path_to_carrier_names=snakemake.input.carrier_names,
        first_year=snakemake.params.first_year,
        path_to_result=snakemake.output[0],
    )
