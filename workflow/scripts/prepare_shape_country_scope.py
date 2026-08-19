"""Prepare country lists needed by the shape-specific heat workflow."""

import sys
from typing import TYPE_CHECKING, Any

import _schemas
import geopandas as gpd

if TYPE_CHECKING:
    snakemake: Any

def _country_scope_w_proxies(
    country_ids: list[str],
    proxies: dict[str, list[str]],
    covered_country_ids: list[str],
) -> list[str]:
    expanded_country_ids = set(country_ids)
    for country_id in expanded_country_ids - set(covered_country_ids):
        expanded_country_ids.update(proxies[country_id])
    return sorted(expanded_country_ids)


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
    with open(snakemake.output.country_ids, "w") as f:
        f.write("\n".join(country_ids) + "\n")
    with open(snakemake.output.source_country_ids, "w") as f:
        f.write("\n".join(proxy_country_ids) + "\n")


if __name__ == "__main__":
    sys.stderr = open(snakemake.log[0], "w", buffering=1)
    main()
