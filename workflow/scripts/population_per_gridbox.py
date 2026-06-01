"""Calculate population weights per weather gridbox and shape."""

import math
import warnings

import geopandas as gpd
import pandas as pd
import rioxarray
import xarray as xr
from gregor.aggregate import aggregate_raster_to_polygon

EPSG_3035 = "EPSG:3035"
WGS84 = "EPSG:4326"
GRIDBOX_SIZE = 25000  # MERRA-2 grid


def _normalise_shape_ids(locations: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    required = {"shape_id", "geometry"}
    missing = required.difference(locations.columns)
    if missing:
        raise ValueError(f"Missing required shape columns: {sorted(missing)}")
    if locations.crs is None:
        raise ValueError("The shapes parquet file must define a CRS.")

    locations = locations.rename(columns={"shape_id": "id"}).copy()
    locations["id"] = locations["id"].astype(str).str.replace(".", "-", regex=False)
    if locations["id"].duplicated().any():
        duplicate_ids = sorted(
            locations.loc[locations["id"].duplicated(keep=False), "id"].unique()
        )
        raise ValueError(
            "Shapes contain duplicate IDs after replacing '.' with '-': "
            f"{duplicate_ids}"
        )
    return locations


def population_on_weather_grid(
    path_to_population: str,
    path_to_locations: str,
    path_to_coordinates: str,
    lat_name: str,
    lon_name: str,
    out_path: str,
) -> None:
    """Uses population as a proxy to regionalise heat demand."""
    # We need the coordinates. This can be any file with gridded data across Europe
    # with WGS84 projection which will be used to generate which the grid
    # At minimum, the dataset must contain the `site` coordinate and latitude and
    # longitude variables
    coordinate_ds = xr.open_dataset(path_to_coordinates)

    # Locations are parquet shape files at the resolution of interest.
    locations = _normalise_shape_ids(gpd.read_parquet(path_to_locations))

    gridbox_points = gpd.GeoDataFrame(
        geometry=gpd.points_from_xy(
            coordinate_ds[lon_name].values, coordinate_ds[lat_name].values
        ),
        index=coordinate_ds.site.to_index(),
        crs=WGS84,
    )
    # To go from a grid of points to a grid of boxes filling the entire space,
    # we `buffer` to create a grid of circles whose edges just touch,
    # then we define the envelope of that circle to create a square.
    gridbox_points = gridbox_points.to_crs(EPSG_3035)
    gridbox = gpd.GeoDataFrame(
        gridbox_points.index.to_frame(),
        geometry=gridbox_points.buffer(GRIDBOX_SIZE).envelope,
        crs=EPSG_3035,
    )
    locations_3035 = locations.to_crs(EPSG_3035)
    minx, miny, maxx, maxy = locations_3035.total_bounds
    gridbox = gridbox.cx[
        minx - GRIDBOX_SIZE : maxx + GRIDBOX_SIZE,
        miny - GRIDBOX_SIZE : maxy + GRIDBOX_SIZE,
    ]
    if gridbox.empty:
        raise ValueError("No weather gridboxes overlap the provided shapes.")

    # Create new shapes that are either complete gridboxes or partial ones that
    # sit inside a specific location.
    gridboxes_mapped_to_locations = gpd.overlay(gridbox, locations_3035)
    if gridboxes_mapped_to_locations.empty:
        raise ValueError("No weather gridbox polygons intersect the provided shapes.")
    gridboxes_mapped_to_locations = _assign_unmapped_locations_to_nearest_gridbox(
        gridbox, locations_3035, gridboxes_mapped_to_locations
    )

    population = rioxarray.open_rasterio(path_to_population, masked=True).squeeze(
        drop=True
    )
    population = population.fillna(0)
    gridboxes_mapped_to_locations = _aggregate_population_to_polygons(
        population, gridboxes_mapped_to_locations
    )
    locations = _aggregate_population_to_polygons(population, locations)

    total_population = locations.population.sum()
    assigned_population = gridboxes_mapped_to_locations.population.sum()
    if assigned_population <= 0:
        raise ValueError("No population could be assigned to weather gridboxes.")
    if not math.isclose(total_population, assigned_population, abs_tol=10**3):
        missing_population = total_population - assigned_population
        missing_fraction = missing_population / total_population
        warnings.warn(
            "Population assigned to weather gridboxes differs from total shape "
            "population. This can happen when shapes extend beyond the weather "
            "grid extent. "
            f"Total={total_population:.0f}, assigned={assigned_population:.0f}, "
            f"missing={missing_population:.0f} ({missing_fraction:.2%}).",
            stacklevel=2,
        )

    population_da = xr.DataArray.from_series(
        gridboxes_mapped_to_locations.set_index(["site", "id"]).population
    )
    population_da.to_netcdf(out_path)


def _assign_unmapped_locations_to_nearest_gridbox(
    gridbox: gpd.GeoDataFrame,
    locations: gpd.GeoDataFrame,
    mapped: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """Assign locations without gridbox overlap to the nearest weather gridbox."""
    missing_ids = sorted(set(locations["id"]) - set(mapped["id"]))
    if not missing_ids:
        return mapped

    missing_locations = locations.loc[
        locations["id"].isin(missing_ids), ["id", "geometry"]
    ]
    nearest = gpd.sjoin_nearest(
        missing_locations,
        gridbox[["site", "geometry"]].reset_index(drop=True),
        how="left",
        distance_col="distance_to_weather_gridbox",
    )
    if nearest["site"].isna().any():
        unmapped_ids = sorted(nearest.loc[nearest["site"].isna(), "id"].unique())
        raise ValueError(
            "No nearest weather gridbox could be found for shape IDs: "
            f"{unmapped_ids}"
        )

    max_distance = nearest["distance_to_weather_gridbox"].max()
    warnings.warn(
        "Some shapes do not intersect any weather gridbox and will be assigned "
        "to the nearest gridbox: "
        f"{missing_ids}. Maximum nearest-grid distance is {max_distance:.0f} m.",
        stacklevel=2,
    )
    nearest = nearest[["site", "id", "geometry"]]
    return gpd.GeoDataFrame(
        pd.concat([mapped, nearest], ignore_index=True),
        geometry="geometry",
        crs=mapped.crs,
    )


def _aggregate_population_to_polygons(
    population: xr.DataArray, polygons: gpd.GeoDataFrame
) -> gpd.GeoDataFrame:
    aggregated = aggregate_raster_to_polygon(population, polygons, stats="sum")
    polygons = polygons.copy()
    polygons["population"] = aggregated["sum"].to_numpy()
    return polygons


if __name__ == "__main__":
    population_on_weather_grid(
        path_to_population=snakemake.input.population,
        path_to_locations=snakemake.input.locations,
        path_to_coordinates=snakemake.input.weather_grid,
        lat_name=snakemake.params.lat_name,
        lon_name=snakemake.params.lon_name,
        out_path=snakemake.output[0],
    )
