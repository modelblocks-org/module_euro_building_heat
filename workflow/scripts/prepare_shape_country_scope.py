"""Prepare country lists needed by the shape-specific heat workflow."""

import pandas as pd

ALPHA3_TO_JRC_IDEES = {
    "AUT": "AT",
    "BEL": "BE",
    "BGR": "BG",
    "CYP": "CY",
    "CZE": "CZ",
    "DEU": "DE",
    "DNK": "DK",
    "EST": "EE",
    "GRC": "EL",
    "ESP": "ES",
    "FIN": "FI",
    "FRA": "FR",
    "HRV": "HR",
    "HUN": "HU",
    "IRL": "IE",
    "ITA": "IT",
    "LTU": "LT",
    "LUX": "LU",
    "LVA": "LV",
    "MLT": "MT",
    "NLD": "NL",
    "POL": "PL",
    "PRT": "PT",
    "ROU": "RO",
    "SWE": "SE",
    "SVN": "SI",
    "SVK": "SK",
    "GBR": "UK",
}


def _normalise_country_ids(values: pd.Series) -> list[str]:
    country_ids = values.dropna().astype(str).str.strip().str.upper()
    country_ids = country_ids[country_ids != ""]
    return sorted(country_ids.unique())


def _check_supported_country_ids(
    country_ids: list[str], supported_countries: list[str]
) -> None:
    supported_country_ids = {
        str(country_id).strip().upper()
        for country_id in supported_countries
        if str(country_id).strip()
    }
    unsupported_country_ids = sorted(set(country_ids) - supported_country_ids)
    if unsupported_country_ids:
        raise ValueError(
            "The shapes file requests countries that this module cannot process: "
            f"{unsupported_country_ids}. Remove these countries from the shapes "
            "input or add the required data support before running the workflow."
        )


def _jrc_idees_scope(
    country_ids: list[str],
    fill_missing_values: dict[str, list[str]],
    jrc_idees_spatial_scope: list[str],
) -> list[str]:
    reference_countries = {
        reference
        for country_id in country_ids
        for reference in fill_missing_values.get(country_id, [])
    }
    required_country_ids = set(country_ids) | reference_countries
    return sorted(
        jrc_code
        for country_id in required_country_ids
        if (jrc_code := ALPHA3_TO_JRC_IDEES.get(country_id)) in jrc_idees_spatial_scope
    )


if __name__ == "__main__":
    shapes = pd.read_parquet(snakemake.input.shapes)
    if "country_id" not in shapes.columns:
        raise ValueError("The shapes parquet file must include a 'country_id' column.")

    country_ids = _normalise_country_ids(shapes["country_id"])
    if not country_ids:
        raise ValueError("The shapes parquet file does not contain any country IDs.")
    _check_supported_country_ids(
        country_ids,
        snakemake.params.supported_countries,
    )
    jrc_idees_country_codes = _jrc_idees_scope(
        country_ids,
        snakemake.params.fill_missing_values,
        snakemake.params.jrc_idees_spatial_scope,
    )

    with open(snakemake.output.country_ids, "w") as f:
        f.write("\n".join(country_ids) + "\n")
    with open(snakemake.output.jrc_idees_country_codes, "w") as f:
        f.write("\n".join(jrc_idees_country_codes) + "\n")
