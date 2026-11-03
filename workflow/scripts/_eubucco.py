"""Shared EUBUCCO region-mapping and canonical-table operations.

Map legacy NUTS 2016 regions to the workflow's current NUTS-3 geography and
convert building footprints to a canonical Arrow schema. Building region
IDs use the source geography. Downstream allocation assigns centroids to
current control regions.

Sources:
    EUBUCCO data and schema: https://docs.eubucco.com/v0.2/
    EUBUCCO data descriptor: https://doi.org/10.1038/s41597-023-02040-2
"""

import json
from decimal import Decimal
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds
import shapely
from _schemas import EUBUCCO_PROCESSED_SCHEMA
from pyproj import Transformer


def repair_height_floors(batch: pa.RecordBatch, settings: dict) -> pa.RecordBatch:
    """Flag original ratio errors and optionally repair suspect attributes.

    Compare source decimals before float conversion to retain exact boundary
    pairs. Replacement storeys may be fractional; rounding would bias floor area.
    Preserve the original flag so dominance removal cannot act on sound pairs.
    """
    height = (
        pc.cast(batch.column("height"), pa.float64())
        .to_numpy(zero_copy_only=False)
        .copy()
    )
    floors = (
        pc.cast(batch.column("floors"), pa.float64())
        .to_numpy(zero_copy_only=False)
        .copy()
    )
    valid_height = np.isfinite(height) & (height > 0)
    valid_floors = np.isfinite(floors) & (floors > 0)
    inconsistent = pc.fill_null(
        pc.less(
            batch.column("height"),
            pc.multiply(
                batch.column("floors"),
                pa.scalar(Decimal(str(settings["minimum_height_per_floor_m"]))),
            ),
        ),
        False,
    ).to_numpy(zero_copy_only=False)
    batch = batch.append_column(
        "height_floor_ratio_flagged",
        pa.array(inconsistent & np.isfinite(height) & valid_floors),
    )
    if not settings["repair_height_floors"]:
        return batch
    repair = ~valid_height | ~valid_floors | inconsistent
    both = repair & valid_height & valid_floors
    keep_height = valid_height.copy()
    if both.any():
        priority = {
            source: rank for rank, source in enumerate(settings["source_priority"])
        }
        ranks = []
        for column in ("height_source", "floors_source"):
            values = batch.column(column).filter(pa.array(both)).to_pylist()
            normalized = [
                "gov" if isinstance(v, str) and v.startswith("gov-") else v
                for v in values
            ]
            ranks.append(np.array([priority[v] for v in normalized]))
        keep_height[both] = ranks[0] <= ranks[1]
    replace_floors = repair & keep_height
    replace_height = repair & ~keep_height
    floors[replace_floors] = (
        height[replace_floors] / settings["reference_floor_height_m"]
    )
    height[replace_height] = (
        floors[replace_height] * settings["reference_floor_height_m"]
    )
    for name, values in (("height", height), ("floors", floors)):
        batch = batch.set_column(
            batch.schema.get_field_index(name), name, pa.array(values)
        )
    return batch


def canonical_from_footprints(
    batch: pa.RecordBatch,
    transformer: Transformer,
    footprint_threshold_m2: float | None,
) -> pa.Table:
    """Retain building measures, centroids and selectively stored large footprints.

    Decode WKB geometry in EPSG:3035, where area and perimeter are measured in
    square metres and metres. The transformer maps these centroids to the
    processing CRS with x/y axis order.
    """
    geometry = shapely.from_wkb(
        pc.cast(batch.column("geometry"), pa.binary()).to_numpy(zero_copy_only=False)
    )
    centroids = shapely.centroid(geometry)
    x, y = transformer.transform(shapely.get_x(centroids), shapely.get_y(centroids))
    area = shapely.area(geometry)
    footprints = np.full(len(geometry), None, dtype=object)
    if footprint_threshold_m2 is not None:
        large = area > footprint_threshold_m2
        footprints[large] = shapely.to_wkb(
            shapely.transform(geometry[large], transformer.transform, interleaved=False)
        )
    return pa.Table.from_pydict(
        {
            "id": batch.column("id"),
            "region_id": batch.column("region_id"),
            "type": batch.column("type"),
            "subtype": batch.column("subtype"),
            "floors": batch.column("floors"),
            "footprint_area_m2": area,
            "footprint_perimeter_m": shapely.length(geometry),
            "height_m": batch.column("height"),
            "x": x,
            "y": y,
            "height_floor_ratio_flagged": batch.column("height_floor_ratio_flagged"),
            "footprint_wkb": footprints,
        },
        schema=EUBUCCO_PROCESSED_SCHEMA,
    )


def eubucco_batch_filter(region_ids: list[str], bounds: np.ndarray) -> ds.Expression:
    """Build an Arrow filter for legacy IDs and processing-CRS centroid bounds.

    Bounds are (left, bottom, right, top) for complete current control regions.
    Callers must test the selected centroids against individual region polygons
    before allocating regional totals.
    """
    left, bottom, right, top = bounds
    return (
        ds.field("region_id").isin(pa.array(region_ids, type=pa.string()))
        & (ds.field("x") >= left)
        & (ds.field("x") <= right)
        & (ds.field("y") >= bottom)
        & (ds.field("y") <= top)
    )


def assign_region_batches(
    mapping: dict[str, dict], stats: pd.DataFrame, batch_count: int
) -> dict[str, list[str]]:
    """Balance current NUTS-3 regions over deterministic processing batches.

    Current NUTS-2 groups stay together so neighbouring current regions reuse
    overlapping legacy EUBUCCO selections. Unique EUBUCCO building counts
    approximate each group's processing cost; largest-first assignment limits
    stragglers while identifiers provide deterministic tie-breaking.

    Mapping entries contain ``eubucco_region_ids``; statistics contain the
    legacy ``region_id`` and building count ``n``. Return zero-padded batch IDs
    mapped to sorted current-region IDs, with at most ``batch_count`` batches.
    """
    counts = stats.set_index("region_id").n
    groups: dict[str, list[str]] = {}
    for region in mapping:
        # Group NUTS-3 IDs by NUTS-2 prefix and keep shape-based regions separate.
        group = region[:4] if len(region) == 5 else region
        groups.setdefault(group, []).append(region)
    # Count each legacy region once per group, even when current regions share it.
    weights = {
        group: int(
            counts.reindex(
                {
                    region_id
                    for nuts3 in regions
                    for region_id in mapping[nuts3]["eubucco_region_ids"]
                }
            )
            .fillna(0)
            .sum()
        )
        for group, regions in groups.items()
    }

    size = min(batch_count, len(groups))
    batches: dict[str, list[str]] = {f"{index:03d}": [] for index in range(size)}
    loads = {batch: 0 for batch in batches}
    ordered = sorted(groups, key=lambda item: (-weights[item], item))
    for index, group in enumerate(ordered):
        # Seed every batch before assigning remaining groups to the lightest load.
        batch = (
            f"{index:03d}"
            if index < size
            else min(loads, key=lambda item: (loads[item], item))
        )
        batches[batch].extend(groups[group])
        loads[batch] += weights[group]
    return {batch: sorted(regions) for batch, regions in batches.items()}


def map_regions(
    nuts3: gpd.GeoDataFrame,
    eubucco_regions: gpd.GeoDataFrame,
    covered_regions: pd.Series,
) -> dict[str, dict[str, list[str]]]:
    """Map current NUTS-3 polygons to legacy EUBUCCO regions.

    Both GeoDataFrames must use the same projected CRS and contain ``region_id``.
    Only legacy NUTS-3 regions present in ``covered_regions`` are candidates.
    Require matching country prefixes and positive-area overlap to exclude
    cross-country matches and boundary-only touches.

    Return each current ID's sorted legacy ``region_ids`` and parent
    ``nuts2_ids`` for download selection. Regions without coverage retain empty
    lists so source planning can select the Microsoft fallback.
    """
    legacy = eubucco_regions.loc[
        eubucco_regions.region_id.str.len().eq(5)
        & eubucco_regions.region_id.isin(covered_regions)
    ].set_index("region_id")
    mapping = {}
    for row in nuts3.itertuples():
        positions = legacy.sindex.query(row.geometry, predicate="intersects")
        candidates = legacy.iloc[positions]
        candidates = candidates.loc[candidates.index.str[:2] == row.region_id[:2]]
        region_ids = sorted(
            candidates.index[
                candidates.geometry.intersection(row.geometry).area.gt(0)
            ].tolist()
        )
        mapping[row.region_id] = {
            "region_ids": region_ids,
            "nuts2_ids": sorted({region_id[:4] for region_id in region_ids}),
        }
    return mapping


def read_plan(path: str | Path) -> dict:
    """Read a workflow-generated source or batch planning manifest from JSON."""
    with open(path) as stream:
        return json.load(stream)
