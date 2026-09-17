"""Shared floor-area control totals and building-weighted allocation.

Residential totals are reconstructed from Eurostat Census 2021 dwelling counts
by useful-floor-space class. Where countries report rooms instead of area, room
counts are converted with the configured mean area per room. Useful area is then
converted to gross floor area with the configured ratio. Building centroids
locate EUBUCCO or Microsoft building support on the hectare grid. GHS-POP is
used to estimate totals outside Eurostat coverage and to inform heat support.

The use of building stock and population proxies follows the hectare-level
floor-area regionalisation approach described by Müller et al. (2019).

Sources:
    Method: https://doi.org/10.3390/en12244789
    Census definitions: https://ec.europa.eu/eurostat/cache/metadata/en/cens_21_esms.htm
    GHS-POP R2023A: https://human-settlement.emergency.copernicus.eu/documents/GHSL_Data_Package_2023.pdf
"""

from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
from _microsoft import quadkey_polygon
from _schemas import (
    validate_floor_area_allocation,
    validate_nonnegative,
    validate_population_allocation,
)
from _utils import point_grid
from rasterio.features import geometry_mask


def select_building_sectors(buildings, residential_type, commercial_subtypes):
    """Return residential and commercial/public building subsets."""
    return (
        buildings.loc[buildings["type"].eq(residential_type)],
        buildings.loc[buildings["subtype"].isin(commercial_subtypes)],
    )


def census_values(path: str, year: int) -> pd.DataFrame:
    """Read one Eurostat census year into explicit dimension columns.

    Eurostat bulk TSV files encode all non-time dimensions in the first column
    and append observation flags to values. Splitting the series key and parsing
    only the leading numeric token preserves unavailable observations as NaN.
    """
    data = pd.read_csv(path, sep="\t", dtype=str)
    series_key = data.columns[0]
    dimensions = series_key.removesuffix("\\TIME_PERIOD").split(",")
    year_column = next(column for column in data if column.strip() == str(year))
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
    rooms = (
        common.loc[common.area.eq("TOTAL") & common.n_room.isin(settings["rooms"])]
        .pivot_table(index="geo", columns="n_room", values="value", aggfunc="sum")
        .mul(pd.Series(settings["rooms"]))
        .sum(axis=1, min_count=1)
        .mul(settings["floor_area_per_room_m2"])
    )
    return area.where(area.gt(0), rooms).mul(settings["useful_to_gross_ratio"])


def microsoft_population_support(profile, buildings, population, low_quadkeys):
    """Share one population fallback for sparse tiles and missing centroids."""
    good = buildings.loc[~buildings.quadkey.isin(low_quadkeys)].copy()
    missing = point_grid(profile, good, np.ones(len(good))) == 0
    if low_quadkeys:
        polygons = gpd.GeoSeries(
            [quadkey_polygon(key) for key in sorted(low_quadkeys)], crs=4326
        ).to_crs(profile["crs"])
        missing |= geometry_mask(
            [polygons.union_all()], population.shape, profile["transform"], invert=True
        )
    return good, np.where(missing, population, 0.0)


def microsoft_floor_area_support(
    buildings, proxy_population, sector_total, regional_population
):
    """Allocate population proxies first, then retain the regional sector total."""
    validate_population_allocation(proxy_population, sector_total, regional_population)
    proxy = np.zeros_like(proxy_population)
    if sector_total > 0 and proxy_population.sum() > 0:
        proxy = proxy_population * (sector_total / regional_population)
    good = buildings.copy()
    support = good.footprint_area_m2.sum()
    # A complete population allocation may exceed its total by roundoff only.
    remaining = max(0.0, sector_total - proxy.sum())
    validate_floor_area_allocation(support, remaining, proxy.sum(), sector_total)
    good["floor_area_m2"] = (
        good.footprint_area_m2 * remaining / support if support > 0 else 0.0
    )
    validate_nonnegative(good.floor_area_m2.to_numpy(), proxy)
    validate_floor_area_allocation(
        support, good.floor_area_m2.sum(), proxy.sum(), sector_total
    )
    return good, proxy
