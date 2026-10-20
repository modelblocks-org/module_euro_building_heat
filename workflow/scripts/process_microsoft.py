"""Assign Microsoft footprints to control regions and combine their partitions.

Input features are validated as they are read. Geometry normalization is
part of conversion: project WGS84 footprints, measure their area, and assign
centroids to regions. Shape-based fallback regions take precedence if control
regions overlap. Raw tile counts are retained for the sparse-coverage policy.

Source: https://github.com/microsoft/GlobalMLBuildingFootprints
"""

import gzip
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any

import duckdb
import geopandas as gpd
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import shapely
from _eubucco import read_plan
from _microsoft import (
    MICROSOFT_COLUMNS,
    MICROSOFT_SCHEMA,
    MICROSOFT_TILE_STATISTICS_SCHEMA,
    tile_statistics,
)
from _schemas import validate_microsoft_feature
from _utils import output_profile

if TYPE_CHECKING:
    snakemake: Any

sys.stderr = open(snakemake.log[0], "w")
Path(snakemake.output.table).parent.mkdir(parents=True, exist_ok=True)
temporary = TemporaryDirectory(prefix="heat_buildings_")
plan = read_plan(snakemake.input.plan)
regions = gpd.read_parquet(snakemake.input.regions).set_index("region_id")
selected = [
    region_id
    for region_id, settings in plan["regions"].items()
    if "microsoft" in {settings["residential_source"], settings["commercial_source"]}
]
targets = regions.loc[selected, ["geometry"]]
threshold_cells = snakemake.params.footprint_allocation_threshold_cells
cell_area = abs(
    output_profile(regions.total_bounds, regions.crs)["transform"].determinant
)
output = Path(temporary.name)
planned_quadkeys = sorted(
    {
        key
        for region_id in selected
        for key in plan["regions"][region_id]["microsoft_quadkeys"]
    }
)


def write_batch(source, key, batch_number, ids, geometries):
    """Project, assign and persist one bounded GeoJSONL batch."""
    footprints = gpd.GeoDataFrame({"id": ids}, geometry=geometries, crs=4326).to_crs(
        regions.crs
    )
    points = footprints.copy()
    points.geometry = footprints.centroid
    assigned = gpd.sjoin(points, targets, predicate="within", how="inner")
    assigned["fallback"] = assigned.region_id.str.startswith("shape-")
    assigned = assigned.sort_values(
        ["id", "fallback", "region_id"], ascending=[True, False, True]
    ).drop_duplicates("id")
    if assigned.empty:
        return
    area = footprints.geometry.area.reindex(assigned.index)
    footprint_wkb = pd.Series(None, index=assigned.index, dtype=object)
    if threshold_cells is not None:
        oversized = area > threshold_cells * cell_area
        footprint_wkb.loc[oversized] = footprints.geometry.reindex(
            assigned.index[oversized]
        ).to_wkb()
    table = pa.Table.from_arrays(
        [
            pa.array(assigned.id),
            pa.array([key] * len(assigned)),
            pa.array(assigned.region_id),
            pa.array(area),
            pa.array(assigned.geometry.x),
            pa.array(assigned.geometry.y),
            pa.array(footprint_wkb, type=pa.binary()),
        ],
        schema=MICROSOFT_SCHEMA,
    )
    path = output / f"{source.stem}-{batch_number}.parquet"
    pq.write_table(table, path, compression="zstd", row_group_size=100_000)


source_counts = []
for source in sorted(Path(source) for source in snakemake.input.downloads):
    key = source.name[:9]
    ids, geometries = [], []
    raw_count = 0
    batch_number = 0
    with gzip.open(source, "rt") as stream:
        for line_number, line in enumerate(stream):
            raw_count += 1
            feature = json.loads(line)
            validate_microsoft_feature(feature)
            ids.append(f"{source.stem}:{line_number}")
            geometries.append(
                shapely.make_valid(
                    shapely.geometry.shape(feature["geometry"]),
                    method="structure",
                    keep_collapsed=False,
                )
            )
            if len(ids) == 100_000:
                write_batch(source, key, batch_number, ids, geometries)
                ids, geometries = [], []
                batch_number += 1
    if ids:
        write_batch(source, key, batch_number, ids, geometries)
    source_counts.append((key, raw_count))

statistics = tile_statistics(planned_quadkeys, source_counts)
pq.write_table(
    pa.Table.from_pandas(
        statistics, schema=MICROSOFT_TILE_STATISTICS_SCHEMA, preserve_index=False
    ),
    snakemake.output.statistics,
)


# External sorting bounds memory while producing a reusable regional table.
partitions = sorted(output.glob("*.parquet"))
if partitions:
    columns = ", ".join(MICROSOFT_COLUMNS)
    source = output / "*.parquet"
    duckdb.connect().execute(
        f"""COPY (SELECT {columns} FROM read_parquet('{source}') ORDER BY region_id, id)
        TO '{snakemake.output.table}' (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)"""
    )
else:
    pq.write_table(
        pa.Table.from_batches([], schema=MICROSOFT_SCHEMA), snakemake.output.table
    )


# Cache regional footprint totals for floor-area allocation.
pq.write_table(
    duckdb.connect()
    .execute(
        "SELECT region_id, sum(footprint_area_m2)::DOUBLE AS footprint_area_m2 "
        "FROM read_parquet(?) GROUP BY region_id ORDER BY region_id",
        [str(snakemake.output.table)],
    )
    .to_arrow_table(),
    snakemake.output.totals,
)


temporary.cleanup()
