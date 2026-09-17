"""Shared EUBUCCO region-mapping and canonical-table operations.

Map legacy NUTS 2016 regions to the workflow's current NUTS-3 geography and
convert both building distributions to a shared Arrow schema. Building region
IDs remain in the legacy geography; downstream allocation assigns centroids
to current control regions.

Sources:
    EUBUCCO data and schema: https://docs.eubucco.com/v0.2/
    EUBUCCO data descriptor: https://doi.org/10.1038/s41597-023-02040-2
"""

import json
from pathlib import Path

import geopandas as gpd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds
import shapely

# Both distributions use metric building measures and centroid coordinates in
# the workflow's processing CRS. Lightweight data have no observed perimeter.
EUBUCCO_COLUMNS = [
    "id",
    "region_id",
    "type",
    "subtype",
    "floors",
    "footprint_area_m2",
    "footprint_perimeter_m",
    "height_m",
    "x",
    "y",
]
EUBUCCO_SCHEMA = pa.schema(
    [
        ("id", pa.string()),
        ("region_id", pa.string()),
        ("type", pa.string()),
        ("subtype", pa.string()),
        ("floors", pa.float64()),
        ("footprint_area_m2", pa.float64()),
        ("footprint_perimeter_m", pa.float64()),
        ("height_m", pa.float64()),
        ("x", pa.float64()),
        ("y", pa.float64()),
    ]
)


def canonical_from_lightweight(batch: pa.RecordBatch, transformer) -> pa.Table:
    """Convert lightweight building attributes to the shared output schema.

    The transformer must map WGS84 longitude/latitude to the processing CRS
    with x/y axis order. Retain the source footprint area and height in square
    metres and metres; leave perimeter null because footprints are unavailable.
    """
    x, y = transformer.transform(
        batch.column("lon").to_numpy(zero_copy_only=False),
        batch.column("lat").to_numpy(zero_copy_only=False),
    )
    return pa.Table.from_arrays(
        [
            pc.cast(batch.column("id"), pa.string()),
            pc.cast(batch.column("region_id"), pa.string()),
            pc.cast(batch.column("type"), pa.string()),
            pc.cast(batch.column("subtype"), pa.string()),
            pc.cast(batch.column("floors"), pa.float64()),
            pc.cast(batch.column("footprint_area"), pa.float64()),
            pa.nulls(batch.num_rows, pa.float64()),
            pc.cast(batch.column("height"), pa.float64()),
            pa.array(x),
            pa.array(y),
        ],
        schema=EUBUCCO_SCHEMA,
    )


def canonical_from_full(batch: pa.RecordBatch, transformer) -> pa.Table:
    """Reduce full-distribution footprints to building measures and centroids.

    Decode WKB geometry in EPSG:3035, where area and perimeter are measured in
    square metres and metres. The transformer maps these centroids to the
    processing CRS with x/y axis order; polygon geometry is not retained.
    """
    geometry = shapely.from_wkb(
        pc.cast(batch.column("geometry"), pa.binary()).to_numpy(zero_copy_only=False)
    )
    centroids = shapely.centroid(geometry)
    x, y = transformer.transform(shapely.get_x(centroids), shapely.get_y(centroids))
    return pa.Table.from_arrays(
        [
            pc.cast(batch.column("id"), pa.string()),
            pc.cast(batch.column("region_id"), pa.string()),
            pc.cast(batch.column("type"), pa.string()),
            pc.cast(batch.column("subtype"), pa.string()),
            pc.cast(batch.column("floors"), pa.float64()),
            pa.array(shapely.area(geometry)),
            pa.array(shapely.length(geometry)),
            pc.cast(batch.column("height"), pa.float64()),
            pa.array(x),
            pa.array(y),
        ],
        schema=EUBUCCO_SCHEMA,
    )


def eubucco_batch_filter(region_ids, bounds):
    """Build an Arrow filter for legacy IDs and processing-CRS centroid bounds.

    Bounds are (left, bottom, right, top) for complete current control regions.
    This coarse selection limits reads; callers must still test centroids
    against individual region polygons before allocating regional totals.
    """
    left, bottom, right, top = bounds
    return (
        ds.field("region_id").isin(pa.array(region_ids, type=pa.string()))
        & (ds.field("x") >= left)
        & (ds.field("x") <= right)
        & (ds.field("y") >= bottom)
        & (ds.field("y") <= top)
    )


def assign_region_batches(mapping, stats, batch_count: int) -> dict[str, list[str]]:
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
        # Five-character NUTS-3 IDs share a NUTS-2 prefix; shape fallbacks stand alone.
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
    nuts3: gpd.GeoDataFrame, eubucco_regions: gpd.GeoDataFrame, covered_regions
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
