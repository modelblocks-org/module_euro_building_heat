"""Filter user-provided shapes to the processable land scope."""

import sys
from typing import TYPE_CHECKING, Any

import geopandas as gpd
from _schemas import ShapesSchema
from _utils import processing_crs, scope_geometry

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
    shapes = ShapesSchema.validate(shapes.loc[shapes.shape_class.eq("land")]).copy()
    shapes = shapes.to_crs(WGS84)
    shapes.to_parquet(snakemake.output.shapes, index=False)
    crs = processing_crs(shapes)
    scope = gpd.GeoDataFrame(geometry=[scope_geometry(shapes, crs)], crs=crs)
    scope.to_parquet(snakemake.output.scope, index=False)


if __name__ == "__main__":
    sys.stderr = open(snakemake.log[0], "w", buffering=1)
    main()
