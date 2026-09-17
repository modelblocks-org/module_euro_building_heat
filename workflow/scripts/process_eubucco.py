"""Create canonical points from the configured pinned EUBUCCO distribution.

Only buildings assigned to legacy NUTS-3 regions intersecting the requested
case are retained. Lightweight centroids are projected from WGS84; full
EPSG:3035 footprints are reduced to centroids, areas, and perimeters. Batch
processing bounds memory, and every partition uses the strict canonical schema.

Sources:
    EUBUCCO v0.2 schema: https://docs.eubucco.com/v0.2/data-format/schema/
    EUBUCCO data descriptor: https://doi.org/10.1038/s41597-023-02040-2
"""

import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any

import duckdb
import geopandas as gpd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from _eubucco import (
    EUBUCCO_COLUMNS,
    EUBUCCO_SCHEMA,
    canonical_from_full,
    canonical_from_lightweight,
    read_plan,
)
from pyproj import Transformer

if TYPE_CHECKING:
    snakemake: Any

sys.stderr = open(snakemake.log[0], "w")
Path(snakemake.output.table).parent.mkdir(parents=True, exist_ok=True)
temporary = TemporaryDirectory(prefix="heat_buildings_")
plan = read_plan(snakemake.input.plan)
regions = gpd.read_parquet(snakemake.input.regions)
output = Path(temporary.name)

# Limit the Europe-wide table to legacy regions intersecting the requested case.
region_ids = sorted(
    {
        region
        for mapping in plan["regions"].values()
        for region in mapping["eubucco_region_ids"]
        if "eubucco" in {mapping["residential_source"], mapping["commercial_source"]}
    }
)
selected = pa.array(region_ids)
if plan["eubucco_source"] == "lightweight":
    sources = [Path(source) for source in snakemake.input.downloads]
    columns = [
        "id",
        "region_id",
        "type",
        "subtype",
        "floors",
        "footprint_area",
        "height",
        "lon",
        "lat",
    ]
    transformer = Transformer.from_crs(4326, regions.crs, always_xy=True)
    convert = canonical_from_lightweight
else:
    sources = sorted(Path(source) for source in snakemake.input.downloads)
    columns = ["id", "region_id", "type", "subtype", "floors", "height", "geometry"]
    transformer = Transformer.from_crs(3035, regions.crs, always_xy=True)
    convert = canonical_from_full

for source in sources:
    for index, batch in enumerate(
        pq.ParquetFile(source).iter_batches(batch_size=250_000, columns=columns)
    ):
        filtered = batch.filter(pc.is_in(batch.column("region_id"), selected))
        if not filtered.num_rows:
            continue
        pq.write_table(
            convert(filtered, transformer),
            output / f"{source.stem}-{index}.parquet",
            compression="zstd",
            row_group_size=100_000,
        )


# External sorting bounds memory while producing a reusable regional table.
# Sort partitions deterministically before exposing the regional building table.
partitions = sorted(output.glob("*.parquet"))
if partitions:
    # DuckDB performs the external sort without loading all buildings into memory.
    columns = ", ".join(EUBUCCO_COLUMNS)
    source = output / "*.parquet"
    connection = duckdb.connect()
    connection.execute("SET threads=1")
    connection.execute(
        f"""
        COPY (SELECT {columns} FROM read_parquet('{source}') ORDER BY region_id, id)
        TO '{snakemake.output.table}'
        (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)
        """
    )
else:
    # Preserve the canonical schema when the requested area contains no buildings.
    pq.write_table(
        pa.Table.from_batches([], schema=EUBUCCO_SCHEMA), snakemake.output.table
    )


temporary.cleanup()
