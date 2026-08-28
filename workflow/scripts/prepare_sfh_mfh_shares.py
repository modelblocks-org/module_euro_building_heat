"""Calculate national SFH/MFH shares from the 2021 Eurostat census."""

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import _utils
import pandas as pd

if TYPE_CHECKING:
    snakemake: Any

BUILDING_TYPE = {"RES1": "SFH", "RES2": "MFH", "RES_GE3": "MFH"}


def _read_eurostat_values(path: str) -> pd.DataFrame:
    """Read the Eurostat TSV and separate its series-key dimensions."""
    data = pd.read_csv(path, sep="\t", dtype=str)
    series_key = data.columns[0]
    dimensions = series_key.removesuffix("\\TIME_PERIOD").split(",")
    values = data.iloc[:, 1].str.strip().str.split().str[0]
    data[dimensions] = data.pop(series_key).str.split(",", expand=True)
    data["value"] = pd.to_numeric(values, errors="coerce")
    return data


def calculate_sfh_mfh_shares(path: str) -> pd.DataFrame:
    """Return dwelling-weighted SFH/MFH shares for each reporting country."""
    data = _read_eurostat_values(path)
    selected = data.loc[
        data["freq"].eq("A")
        & data["housing"].eq("DW")
        & data["building"].isin(BUILDING_TYPE)
        & data["unit"].eq("NR")
        & data["geo"].str.fullmatch(r"[A-Z]{2}")
    ].copy()
    selected["building"] = selected["building"].map(BUILDING_TYPE)

    counts = selected.pivot_table(
        index="geo", columns="building", values="value", aggfunc="sum"
    )
    counts.index = pd.Index(
        [_utils.eurostat_to_alpha3(geo) for geo in counts.index], name="country_id"
    )
    shares = counts.div(counts.sum(axis=1), axis=0)[["SFH", "MFH"]]
    return shares.sort_index()


def prepare_country_shares(
    shares: pd.DataFrame, country_ids: list[str], proxies: dict[str, list[str]]
) -> pd.DataFrame:
    """Add missing countries using mean shares from configured references."""
    additions = {}
    for country_id in country_ids:
        if country_id in shares.index:
            continue
        references = proxies.get(country_id)

        additions[country_id] = shares.loc[references].mean()

    if additions:
        shares = pd.concat([shares, pd.DataFrame.from_dict(additions, orient="index")])
    return shares.loc[country_ids].rename_axis(index="country_id")


def main() -> None:
    """Write requested and proxied country shares for the heat-profile workflow."""
    shares = calculate_sfh_mfh_shares(snakemake.input.census)
    country_ids = sorted(
        pd.read_parquet(snakemake.input.shapes, columns=["country_id"])[
            "country_id"
        ].unique()
    )
    shares = prepare_country_shares(shares, country_ids, snakemake.params.proxies)
    Path(snakemake.output[0]).parent.mkdir(parents=True, exist_ok=True)
    shares.to_csv(snakemake.output[0], float_format="%.10f")


if __name__ == "__main__":
    sys.stderr = open(snakemake.log[0], "w", buffering=1)
    main()
