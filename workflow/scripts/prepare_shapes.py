"""Filter user-provided shapes to the processable land scope."""

import sys
from typing import TYPE_CHECKING, Any

import geopandas as gpd
from _schemas import ShapesSchema

if TYPE_CHECKING:
    snakemake: Any

WGS84 = "EPSG:4326"


def check_proxied_country_scope(
    shapes: gpd.GeoDataFrame, dataset_scopes: dict, data_proxies: dict
) -> None:
    """Country scope must be fully covered by the configured proxies."""
    shape_country_ids = set(shapes["country_id"].unique())
    failures = []
    for dataset_name, scope in dataset_scopes.items():
        dataset_country_ids = set(scope["countries"])
        proxies = {
            country_id: set(proxy_country_ids)
            for country_id, proxy_country_ids in data_proxies.get(
                scope["proxy_config"], {}
            ).items()
        }

        for country_id in sorted(shape_country_ids - dataset_country_ids):
            proxy_country_ids = proxies.get(country_id, set())
            missing_scope = sorted(proxy_country_ids - dataset_country_ids)
            missing_shapes = sorted(proxy_country_ids - shape_country_ids)
            if (
                proxy_country_ids
                and not missing_scope
                and (not scope["proxy_requires_shape_population"] or not missing_shapes)
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


def main() -> None:
    """Main snakemake process."""
    shapes = gpd.read_parquet(snakemake.input.shapes)
    if shapes.crs is None:
        raise ValueError("The shapes GeoParquet file must define a CRS.")
    shapes = shapes.to_crs(WGS84)
    shapes = shapes.loc[shapes["shape_class"] == "land"]
    shapes = ShapesSchema.validate(shapes)
    if shapes.empty:
        raise ValueError("No land shapes remain after filtering non-land regions.")

    check_proxied_country_scope(
        shapes, snakemake.params.dataset_scopes, snakemake.params.data_proxies
    )
    shapes.to_parquet(snakemake.output[0])


if __name__ == "__main__":
    sys.stderr = open(snakemake.log[0], "w", buffering=1)
    main()
