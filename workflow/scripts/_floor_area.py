"""Shared floor-area control totals and building-weighted allocation.

Residential totals are reconstructed from Eurostat Census 2021 dwelling counts
by useful-floor-space class. Where countries report rooms instead of area, room
counts are converted with the configured mean area per room. Useful area is then
converted to gross floor area with the configured ratio. Building centroids
locate support on the hectare grid, with oversized footprints allocated
by cell overlap. GHS-POP is
used to estimate totals outside Eurostat coverage and to inform heat support.

The use of building stock and population proxies follows the hectare-level
floor-area regionalisation approach described by Müller et al. (2019).

Sources:
    Method: https://doi.org/10.3390/en12244789
    Census definitions: https://ec.europa.eu/eurostat/cache/metadata/en/cens_21_esms.htm
    GHS-POP R2023A: https://human-settlement.emergency.copernicus.eu/documents/GHSL_Data_Package_2023.pdf
"""

import sys
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from _microsoft import quadkey_polygon
from _utils import point_grid
from rasterio.features import geometry_mask
from rasterio.windows import Window, from_bounds
from rasterio.windows import transform as window_transform


def allocate_building_footprints(
    buildings,
    region,
    profile,
    scope,
    country_land,
    threshold_cells,
    region_id,
    area_column="floor_area_m2",
):
    """Split oversized building footprints into multiple hectare-grid contributions.

    Oversized footprints are those whose area exceeds ``threshold_cells`` times the
    hectare-grid cell area. Building counts are weighted by the fraction of each
    footprint that overlaps the hectare grid. Buildings spanning multiple regions are clipped to the current region. 

    """
    result = buildings.copy()
    result["count_weight"] = 1.0
    result["in_scope"] = shapely.contains_xy(scope, result.x, result.y)
    if threshold_cells is None or result.empty:
        return result
    transform = profile["transform"]
    large = result.footprint_area_m2 > threshold_cells * abs(transform.determinant)
    parts = [result.loc[~large]]
    contributions = 0
    for position in np.flatnonzero(large.to_numpy()):
        building = result.iloc[position]
        label = f"{region_id}/{building.id}"
        if building.footprint_wkb is None:
            raise ValueError(f"Missing oversized footprint for {label}")
        try:
            geometry = shapely.from_wkb(building.footprint_wkb)
        except shapely.errors.GEOSException as error:
            raise ValueError(f"Invalid oversized footprint for {label}") from error
        if (
            geometry.geom_type not in {"Polygon", "MultiPolygon"}
            or geometry.is_empty
            or not geometry.is_valid
        ):
            raise ValueError(f"Invalid oversized footprint for {label}")
        geometry = geometry.intersection(region)
        if geometry.is_empty:
            raise ValueError(f"No eligible footprint overlap for {label}")
        bounds = from_bounds(*geometry.bounds, transform)
        left = max(0, int(np.floor(bounds.col_off)))
        top = max(0, int(np.floor(bounds.row_off)))
        right = min(profile["width"], int(np.ceil(bounds.col_off + bounds.width)))
        bottom = min(profile["height"], int(np.ceil(bounds.row_off + bounds.height)))
        overlaps = []
        # Bounded windows avoid materializing a large polygon's entire bounding grid.
        for row in range(top, bottom, 256):
            for col in range(left, right, 256):
                window = Window(col, row, min(256, right - col), min(256, bottom - row))
                local = window_transform(window, transform)
                shape = (int(window.height), int(window.width))
                rr, cc = np.indices(shape)
                cx, cy = local * (cc + 0.5, rr + 0.5)
                owner = shapely.contains_xy(region, cx, cy)
                rr, cc = rr[owner], cc[owner]
                x, y = local * (cc + 0.5, rr + 0.5)
                cells = shapely.box(
                    x - transform.a / 2,
                    y + transform.e / 2,
                    x + transform.a / 2,
                    y - transform.e / 2,
                )
                areas = shapely.area(shapely.intersection(cells, geometry))
                positive = areas > 0
                land = geometry_mask([country_land], shape, local, invert=True)
                overlaps.append(
                    (x[positive], y[positive], areas[positive], land[rr, cc][positive])
                )
        if not overlaps or sum(a.sum() for _, _, a, _ in overlaps) <= 0:
            raise ValueError(f"No eligible positive-area cells for {label}")
        x, y, area, inside = (np.concatenate(items) for items in zip(*overlaps))
        fractions = area / area.sum()
        fragments = result.iloc[np.full(len(area), position)].copy()
        fragments["x"], fragments["y"] = x, y
        fragments.geometry = gpd.points_from_xy(x, y, crs=buildings.crs)
        fragments["count_weight"] = fractions
        fragments[area_column] = building[area_column] * fractions
        fragments["in_scope"] = inside.astype(bool)
        parts.append(fragments)
        contributions += len(fragments)
    allocated = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), crs=buildings.crs)
    return allocated


def select_building_sectors(buildings):
    """Return residential and commercial/public building subsets."""
    return (
        buildings.loc[buildings["type"].eq("residential")],
        buildings.loc[buildings["subtype"].isin(["commercial", "public"])],
    )


def filter_dominant_buildings(buildings, sources, maximum_share, region_id):
    """Exclude dominant buildings with an originally inconsistent height/floor ratio.

    Dominant buildings are those whose floor area exceeds ``maximum_share`` of the regional total for their sector. 
    Only EUBUCCO-sourced buildings are checked as these are the only that have both height and floor number.

    Only buildings with a flagged height/floor ratio are considered for removal to avoid overly aggresive building removal. 
    Non-flagged buildings are therefore considered valid and just comparitively large. 
    """
    if maximum_share is None:
        return buildings
    remove = pd.Series(False, index=buildings.index)
    sectors = dict(
        zip(("residential", "commercial"), select_building_sectors(buildings))
    )
    for sector, subset in sectors.items():
        if sources[f"{sector}_source"] != "eubucco":
            continue
        area = subset.floor_area_m2
        if not (np.isfinite(area) & area.gt(0)).all() or area.empty:
            raise ValueError(
                f"Invalid EUBUCCO floor-area support for {region_id}/{sector}; "
                "check height/floor repairs and source attributes"
            )
        remove.loc[subset.index] |= subset.height_floor_ratio_flagged & (
            area / area.sum() > maximum_share
        )
    retained = buildings.loc[~remove]
    for sector, subset in zip(
        ("residential", "commercial"), select_building_sectors(retained)
    ):
        if sources[f"{sector}_source"] == "eubucco" and subset.empty:
            raise ValueError(
                f"Dominance filtering removed all EUBUCCO support for "
                f"{region_id}/{sector}; review maximum_regional_floor_area_share"
            )
    return retained


def census_values(path: str) -> pd.DataFrame:
    """Read Eurostat Census 2021 values into explicit dimension columns.

    Eurostat bulk TSV files encode all non-time dimensions in the first column
    and append observation flags to values. Splitting the series key and parsing
    only the leading numeric token preserves unavailable observations as NaN.
    """
    data = pd.read_csv(path, sep="\t", dtype=str)
    series_key = data.columns[0]
    dimensions = series_key.removesuffix("\\TIME_PERIOD").split(",")
    year_column = next(column for column in data if column.strip() == "2021")
    data[dimensions] = data.pop(series_key).str.split(",", expand=True)
    data["value"] = pd.to_numeric(
        data.pop(year_column).str.strip().str.split().str[0], errors="coerce"
    )
    return data


def residential_floor_area(data: pd.DataFrame, settings: dict[str, Any]) -> pd.Series:
    """Calculate NUTS-3 gross residential floor area in square metres.

    Dwelling counts are multiplied by representative areas for their reported
    floor-space classes. If that estimate is absent or zero, room-class counts
    are multiplied by representative room counts and ``floor_area_per_room_m2``.
    The selected useful-area estimate is finally multiplied by
    ``useful_to_gross_ratio``. All class representatives and conversion factors
    are explicit assumptions in ``config/config.yaml``.
    """
    common = data.loc[
        data.freq.eq("A") & data.building.eq("TOTAL") & data.unit.eq("NR")
    ]
    area = (
        common.loc[
            common.n_room.eq("TOTAL") & common.area.isin(settings["floor_space_m2"])
        ]
        .pivot_table(index="geo", columns="area", values="value", aggfunc="sum")
        .mul(pd.Series(settings["floor_space_m2"]))
        .sum(axis=1, min_count=1)
    )
    # Classes 1–8 report exact counts; only the open-ended class needs an assumption.
    room_counts = {str(count): count for count in range(1, 9)} | settings["rooms"]
    rooms = (
        common.loc[common.area.eq("TOTAL") & common.n_room.isin(room_counts)]
        .pivot_table(index="geo", columns="n_room", values="value", aggfunc="sum")
        .mul(pd.Series(room_counts))
        .sum(axis=1, min_count=1)
        .mul(settings["floor_area_per_room_m2"])
    )
    return area.where(area.gt(0), rooms).mul(settings["useful_to_gross_ratio"])


def microsoft_population_support(
    profile, buildings, population, low_quadkeys, footprint_allocation
):
    """Use population in cells without retained Microsoft contributions.
    """
    missing = point_grid(profile, buildings, buildings.count_weight) == 0
    if not footprint_allocation and low_quadkeys:
        polygons = gpd.GeoSeries(
            [quadkey_polygon(key) for key in sorted(low_quadkeys)], crs=4326
        ).to_crs(profile["crs"])
        missing |= geometry_mask(
            [polygons.union_all()], population.shape, profile["transform"], invert=True
        )
    return np.where(missing, population, 0.0)


def microsoft_floor_area_support(
    buildings, proxy_population, sector_total, regional_population
):
    """Allocate population proxies first, then retain the regional sector total."""
    proxy = np.zeros_like(proxy_population)
    if sector_total > 0 and proxy_population.sum() > 0:
        proxy = proxy_population * (sector_total / regional_population)
    good = buildings.copy()
    support = good.footprint_support_m2.sum()
    # A complete population allocation may exceed its total by roundoff only.
    remaining = max(0.0, sector_total - proxy.sum())
    good["floor_area_m2"] = (
        good.footprint_support_m2 * remaining / support if support > 0 else 0.0
    )
    return good, proxy
