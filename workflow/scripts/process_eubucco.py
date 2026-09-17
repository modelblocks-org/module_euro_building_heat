"""Create canonical points from the configured pinned EUBUCCO distribution.

Only buildings assigned to legacy NUTS-3 regions intersecting the requested
case are retained. Lightweight centroids are projected from WGS84; full
EPSG:3035 footprints are reduced to centroids, areas, and perimeters. Batch
processing bounds memory, and every partition uses the strict canonical schema.
The output Parquet table retains legacy region IDs and is sorted by region ID
and building ID. Current-region assignment happens during floor-area allocation.

Sources:
    EUBUCCO v0.2 schema: https://docs.eubucco.com/v0.2/data-format/schema/
    EUBUCCO data descriptor: https://doi.org/10.1038/s41597-023-02040-2
"""

import shutil
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
with TemporaryDirectory(prefix="heat_buildings_") as temporary:
    plan = read_plan(snakemake.input.plan)
    regions = gpd.read_parquet(snakemake.input.regions)
    output = Path(temporary)

    # Limit the Europe-wide table to legacy regions intersecting the requested case.
    region_ids = sorted(
        {
            region
            for mapping in plan["regions"].values()
            for region in mapping["eubucco_region_ids"]
            if "eubucco"
            in {mapping["residential_source"], mapping["commercial_source"]}
        }
    )
    selected = pa.array(region_ids)
    # Lightweight downloads provide coordinates and area; full downloads provide
    # polygons. Both converters produce the same columns in the processing CRS.
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

    # Filter before geometry conversion and partition by legacy region so the
    # final sort can process one region at a time within the job's memory limit.
    for source in sources:
        for index, batch in enumerate(
            pq.ParquetFile(source).iter_batches(batch_size=250_000, columns=columns)
        ):
            filtered = batch.filter(pc.is_in(batch.column("region_id"), selected))
            if not filtered.num_rows:
                continue
            table = convert(filtered, transformer)
            for region in pc.unique(table["region_id"]).to_pylist():
                # Numeric directory names avoid interpreting region IDs as paths.
                partition = output / str(region_ids.index(region))
                partition.mkdir(exist_ok=True)
                pq.write_table(
                    table.filter(pc.equal(table["region_id"], region)),
                    partition / f"{source.stem}-{index}.parquet",
                    compression="zstd",
                    row_group_size=100_000,
                )

    # Concatenating sorted regions preserves global (region_id, id) order without
    # spilling a Europe-wide sort to disk. Release each region's files as we go.
    # Opening the writer also preserves the schema when no buildings are selected.
    with (
        duckdb.connect() as connection,
        pq.ParquetWriter(
            snakemake.output.table, EUBUCCO_SCHEMA, compression="zstd"
        ) as writer,
    ):
        columns = ", ".join(EUBUCCO_COLUMNS)
        connection.execute("SET threads=1")
        connection.execute("SET preserve_insertion_order=false")
        # Leave room in the job's memory allocation for Arrow and Python.
        connection.execute(
            "SET memory_limit=?", [f"{int(snakemake.resources.mem_mb * 0.75)}MB"]
        )
        connection.execute("SET temp_directory=?", [str(output / "spill")])
        for region_index in range(len(region_ids)):
            partition = output / str(region_index)
            if not partition.exists():
                continue
            batches = connection.execute(
                f"SELECT {columns} FROM read_parquet(?) ORDER BY id",
                [str(partition / "*.parquet")],
            ).fetch_record_batch(rows_per_batch=100_000)
            for batch in batches:
                writer.write_batch(batch, row_group_size=100_000)
            shutil.rmtree(partition)
