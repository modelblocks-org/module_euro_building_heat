"""Prepare country lists needed by the shape-specific heat workflow."""

import sys
from typing import TYPE_CHECKING, Any

import _schemas
import geopandas as gpd
import pycountry

if TYPE_CHECKING:
    snakemake: Any

JRC_IDEES_NON_ISO_CODES = {"GBR": "UK", "GRC": "EL"}


def _country_scope_w_proxies(
    country_ids: list[str],
    proxies: dict[str, list[str]],
    covered_country_ids: list[str],
) -> list[str]:
    expanded_country_ids = set(country_ids)
    for country_id in expanded_country_ids - set(covered_country_ids):
        expanded_country_ids.update(proxies[country_id])
    return sorted(expanded_country_ids)


def _jrc_idees_scope(
    source_country_ids: list[str], jrc_idees_spatial_scope: list[str]
) -> list[str]:
    return sorted(
        jrc_code
        for country_id in source_country_ids
        if (jrc_code := _jrc_idees_country_code(country_id)) in jrc_idees_spatial_scope
    )


def _jrc_idees_country_code(country_id: str) -> str:
    if country_id in JRC_IDEES_NON_ISO_CODES:
        country_jrc = JRC_IDEES_NON_ISO_CODES[country_id]
    else:
        country_jrc = pycountry.countries.lookup(country_id).alpha_2
    return country_jrc


def main() -> None:
    """Main snakemake process."""
    shapes = gpd.read_parquet(snakemake.input.shapes)
    shapes = _schemas.ShapesSchema.validate(shapes)

    country_ids = sorted(shapes["country_id"].unique())

    proxy_country_ids = _country_scope_w_proxies(
        country_ids,
        snakemake.params.jrc_idees_proxies,
        snakemake.params.commercial_end_use_scope,
    )
    jrc_idees_country_codes = _jrc_idees_scope(
        proxy_country_ids, snakemake.params.jrc_idees_spatial_scope
    )

    with open(snakemake.output.country_ids, "w") as f:
        f.write("\n".join(country_ids) + "\n")
    with open(snakemake.output.source_country_ids, "w") as f:
        f.write("\n".join(proxy_country_ids) + "\n")
    with open(snakemake.output.jrc_idees_country_codes, "w") as f:
        f.write("\n".join(jrc_idees_country_codes) + "\n")


if __name__ == "__main__":
    sys.stderr = open(snakemake.log[0], "w", buffering=1)
    main()
