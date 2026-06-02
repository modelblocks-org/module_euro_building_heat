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


def _normalise_country_set(values) -> set[str]:
    return {
        str(country_id).strip().upper()
        for country_id in values
        if str(country_id).strip()
    }


def _normalise_proxy_map(proxy_map: dict | None) -> dict[str, set[str]]:
    if not proxy_map:
        return {}
    return {
        str(country_id).strip().upper(): _normalise_country_set(references)
        for country_id, references in proxy_map.items()
    }


def _check_dataset_country_scope(
    country_ids: list[str], dataset_scopes: dict, data_proxies: dict | None
) -> None:
    requested_country_ids = set(country_ids)
    data_proxies = data_proxies or {}
    failures = []
    for dataset_name, scope in dataset_scopes.items():
        covered_country_ids = _normalise_country_set(scope.get("countries", []))
        proxy_key = scope.get("proxy_config")
        proxies = _normalise_proxy_map(data_proxies.get(proxy_key))

        for country_id in sorted(requested_country_ids - covered_country_ids):
            proxy_country_ids = proxies.get(country_id, set())
            missing_scope = sorted(proxy_country_ids - covered_country_ids)
            missing_shapes = sorted(proxy_country_ids - requested_country_ids)
            if (
                proxy_country_ids
                and not missing_scope
                and (
                    not scope.get("proxy_requires_shape_population", False)
                    or not missing_shapes
                )
            ):
                continue
            failures.append(
                f"{dataset_name}: {country_id}"
                + (
                    " no proxy"
                    if not proxy_country_ids
                    else f" proxies missing from scope {missing_scope}"
                    if missing_scope
                    else f" proxies missing from shapes {missing_shapes}"
                )
            )

    if failures:
        raise ValueError("Unsupported countries: " + "; ".join(failures))


def _expand_country_ids(
    country_ids: list[str],
    proxies: dict[str, list[str]],
    covered_country_ids: set[str],
) -> list[str]:
    proxies = _normalise_proxy_map(proxies)
    expanded_country_ids = set(country_ids)
    changed = True
    while changed:
        changed = False
        for country_id, references in proxies.items():
            if (
                country_id not in expanded_country_ids
                or country_id in covered_country_ids
            ):
                continue
            before = len(expanded_country_ids)
            expanded_country_ids.update(references)
            changed = changed or len(expanded_country_ids) > before
    return sorted(expanded_country_ids)


def _jrc_idees_scope(
    country_ids: list[str],
    jrc_idees_proxies: dict[str, list[str]],
    jrc_idees_spatial_scope: list[str],
    commercial_end_use_scope: list[str],
) -> list[str]:
    required_country_ids = _expand_country_ids(
        country_ids,
        jrc_idees_proxies,
        _normalise_country_set(commercial_end_use_scope),
    )
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
    _check_dataset_country_scope(
        country_ids,
        snakemake.params.dataset_scopes,
        snakemake.params.data_proxies,
    )
    commercial_end_use_scope = snakemake.params.dataset_scopes[
        "commercial_end_use"
    ]["countries"]
    source_country_ids = _expand_country_ids(
        country_ids,
        snakemake.params.data_proxies.get("jrc_idees", {}),
        _normalise_country_set(commercial_end_use_scope),
    )
    jrc_idees_country_codes = _jrc_idees_scope(
        country_ids,
        snakemake.params.data_proxies.get("jrc_idees", {}),
        snakemake.params.jrc_idees_spatial_scope,
        commercial_end_use_scope,
    )

    with open(snakemake.output.country_ids, "w") as f:
        f.write("\n".join(country_ids) + "\n")
    with open(snakemake.output.source_country_ids, "w") as f:
        f.write("\n".join(source_country_ids) + "\n")
    with open(snakemake.output.jrc_idees_country_codes, "w") as f:
        f.write("\n".join(jrc_idees_country_codes) + "\n")
