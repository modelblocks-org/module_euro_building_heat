"""Filter user-provided shapes to the processable land scope."""

from collections.abc import Iterable

import geopandas as gpd


def _normalise_country_ids(values) -> set[str]:
    return {
        str(country_id).strip().upper()
        for country_id in values
        if str(country_id).strip()
    }


def _check_supported_country_ids(shapes: gpd.GeoDataFrame, supported_countries) -> None:
    if supported_countries is None:
        return
    if "country_id" not in shapes.columns:
        raise ValueError("The shapes parquet file must include a 'country_id' column.")

    requested_country_ids = _normalise_country_ids(shapes["country_id"].dropna())
    supported_country_ids = _normalise_country_ids(supported_countries)
    unsupported_country_ids = sorted(requested_country_ids - supported_country_ids)
    if unsupported_country_ids:
        raise ValueError(
            "The shapes file requests countries that this module cannot process: "
            f"{unsupported_country_ids}. Remove these countries from the shapes "
            "input or add the required data support before running the workflow."
        )


def filter_land_shapes(
    input_path: str,
    output_path: str,
    supported_countries: Iterable[str] | None = None,
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

    _check_supported_country_ids(shapes, supported_countries)
    shapes.to_parquet(output_path)


if __name__ == "__main__":
    filter_land_shapes(
        snakemake.input.shapes,
        snakemake.output[0],
        snakemake.params.supported_countries,
    )
