"""Filter user-provided shapes to the processable land scope."""

import geopandas as gpd


def _normalise_country_ids(values) -> set[str]:
    return {
        str(country_id).strip().upper()
        for country_id in values
        if str(country_id).strip()
    }


def _normalise_proxy_map(proxy_map: dict | None) -> dict[str, set[str]]:
    if not proxy_map:
        return {}
    return {
        str(country_id).strip().upper(): _normalise_country_ids(references)
        for country_id, references in proxy_map.items()
    }


def _check_dataset_country_scope(
    shapes: gpd.GeoDataFrame, dataset_scopes: dict | None, data_proxies: dict | None
) -> None:
    if dataset_scopes is None:
        return
    if "country_id" not in shapes.columns:
        raise ValueError("The shapes parquet file must include a 'country_id' column.")

    requested_country_ids = _normalise_country_ids(shapes["country_id"].dropna())
    data_proxies = data_proxies or {}
    failures = []
    for dataset_name, scope in dataset_scopes.items():
        covered_country_ids = _normalise_country_ids(scope.get("countries", []))
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


def filter_land_shapes(
    input_path: str,
    output_path: str,
    dataset_scopes: dict | None = None,
    data_proxies: dict | None = None,
) -> None:
    """Remove marine region shapes from the user-provided shape file."""
    shapes = gpd.read_parquet(input_path)
    if "shape_class" in shapes.columns:
        shapes = shapes.loc[
            shapes["shape_class"].astype(str).str.casefold() != "maritime"
        ].copy()
    elif "shape_id" in shapes.columns:
        shapes = shapes.loc[
            ~shapes["shape_id"]
            .astype(str)
            .str.contains("marineregions", case=False, na=False)
        ].copy()

    if shapes.empty:
        raise ValueError("No land shapes remain after filtering marine regions.")

    _check_dataset_country_scope(shapes, dataset_scopes, data_proxies)
    shapes.to_parquet(output_path)


if __name__ == "__main__":
    filter_land_shapes(
        snakemake.input.shapes,
        snakemake.output[0],
        snakemake.params.dataset_scopes,
        snakemake.params.data_proxies,
    )
