"""Calculate population weights per weather gridbox and shape."""

import math
import warnings

import geopandas as gpd
import numpy as np
import pandas as pd
import rioxarray
import xarray as xr
from gregor.aggregate import aggregate_raster_to_polygon
from shapely.geometry import box

WGS84 = "EPSG:4326"
DEFAULT_GRID_DEGREES = 0.25


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
    coordinate_ds = xr.open_dataset(path_to_coordinates, decode_timedelta=True)

    # Locations are parquet shape files at the resolution of interest.
    locations = _normalise_shape_ids(gpd.read_parquet(path_to_locations))

    population = rioxarray.open_rasterio(path_to_population, masked=True).squeeze(
        drop=True
    )
    population = population.fillna(0)
    gridbox = _weather_gridbox_polygons(coordinate_ds, lat_name, lon_name).to_crs(
        population.rio.crs
    )
    locations = locations.to_crs(population.rio.crs)
    minx, miny, maxx, maxy = locations.total_bounds
    gridbox = gridbox.cx[minx:maxx, miny:maxy]
    if gridbox.empty:
        raise ValueError("No weather gridboxes overlap the provided shapes.")

    # Create new shapes that are either complete gridboxes or partial ones that
    # sit inside a specific location.
    gridboxes_mapped_to_locations = gpd.overlay(gridbox, locations)
    if gridboxes_mapped_to_locations.empty:
        raise ValueError("No weather gridbox polygons intersect the provided shapes.")
    gridboxes_mapped_to_locations = _assign_unmapped_locations_to_nearest_gridbox(
        gridbox, locations, gridboxes_mapped_to_locations
    )

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


def _weather_gridbox_polygons(
    coordinate_ds: xr.Dataset, lat_name: str, lon_name: str
) -> gpd.GeoDataFrame:
    """Build weather grid-cell polygons from grid point coordinates."""
    fallback_step = float(coordinate_ds.attrs.get("grid_degrees", DEFAULT_GRID_DEGREES))
    lat_values = np.asarray(coordinate_ds[lat_name].values, dtype=float)
    lon_values = np.asarray(coordinate_ds[lon_name].values, dtype=float)
    lat_bounds = _coordinate_bounds(lat_values, fallback_step)
    lon_bounds = _coordinate_bounds(lon_values, fallback_step)

    geometries = []
    for lat, lon in zip(lat_values, lon_values, strict=True):
        south, north = lat_bounds[lat]
        west, east = lon_bounds[lon]
        geometries.append(box(west, south, east, north))

    return gpd.GeoDataFrame(
        {"site": coordinate_ds.site.to_index()}, geometry=geometries, crs=WGS84
    )


def _coordinate_bounds(
    values: np.ndarray, fallback_step: float
) -> dict[float, tuple[float, float]]:
    """Infer grid-cell lower and upper bounds for each coordinate value."""
    unique_values = np.sort(np.unique(values.astype(float)))
    if len(unique_values) == 1:
        half_step = fallback_step / 2
        value = unique_values[0]
        return {value: (value - half_step, value + half_step)}

    midpoints = (unique_values[:-1] + unique_values[1:]) / 2
    lower = np.empty_like(unique_values)
    upper = np.empty_like(unique_values)
    lower[0] = unique_values[0] - (unique_values[1] - unique_values[0]) / 2
    lower[1:] = midpoints
    upper[:-1] = midpoints
    upper[-1] = unique_values[-1] + (unique_values[-1] - unique_values[-2]) / 2

    return {
        value: (lower_bound, upper_bound)
        for value, lower_bound, upper_bound in zip(
            unique_values, lower, upper, strict=True
        )
    }


def _assign_unmapped_locations_to_nearest_gridbox(
    gridbox: gpd.GeoDataFrame, locations: gpd.GeoDataFrame, mapped: gpd.GeoDataFrame
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
            f"No nearest weather gridbox could be found for shape IDs: {unmapped_ids}"
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
