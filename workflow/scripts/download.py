"""Download external sources and validate building and population inputs.

Building transfers can resume from a partial file. Where enabled, validation
happens before renaming that file to its cache destination. Existing
pinned building downloads are checked when their download job is rerun.

Sources:
    EUBUCCO: https://docs.eubucco.com/v0.2/data-format/schema/
    Microsoft: https://github.com/microsoft/GlobalMLBuildingFootprints
    Eurostat: https://ec.europa.eu/eurostat/cache/metadata/en/cens_21_esms.htm
"""

import gzip
import json
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import geopandas as gpd
import pandas as pd
from _schemas import (
    EubuccoRegionsSchema,
    EubuccoStatsSchema,
    MicrosoftIndexSchema,
    validate_eubucco_source,
    validate_microsoft_feature,
    validate_population_raster,
)

if TYPE_CHECKING:
    snakemake: Any


def download(job) -> None:
    """Reuse completed files, resume partial transfers, and validate before publishing."""
    kind = job.params.kind
    destination = Path(job.output[0])
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Preserve the format suffix so readers recognize compressed TSV and ZIP files.
    partial = destination.with_name(destination.stem + ".part" + destination.suffix)
    source = destination if destination.exists() else partial
    if source == partial:
        if kind == "microsoft":
            links = pd.read_csv(job.input.index, dtype={"QuadKey": str})
            urls = sorted(links.loc[links.QuadKey.eq(job.wildcards.quadkey), "Url"])
            url = urls[int(job.wildcards.part)]
        else:
            url = job.params.url
        subprocess.run(
            [
                "curl",
                "-fL",
                "--retry",
                "3",
                "--continue-at",
                "-",
                "--output",
                str(source),
                url,
            ],
            check=True,
            stderr=sys.stderr,
        )
    if kind == "population":
        # Inspect the GeoTIFF in place; extraction remains a separate reusable job.
        validate_population_raster(f"/vsizip/{source.resolve()}/{job.params.member}")
    elif kind == "microsoft_index":
        MicrosoftIndexSchema.validate(pd.read_csv(source, dtype={"QuadKey": str}))
    elif kind == "microsoft":
        # Stream the source once; no full tile is held in memory during validation.
        with gzip.open(source, "rt") as stream:
            for line in stream:
                validate_microsoft_feature(json.loads(line))
    elif kind == "eubucco_nuts":
        EubuccoRegionsSchema.validate(
            gpd.read_parquet(source, columns=["region_id", "geometry"])
        )
    elif kind == "eubucco_stats":
        EubuccoStatsSchema.validate(
            gpd.read_parquet(
                source, columns=list(EubuccoStatsSchema.to_schema().columns)
            )
        )
    elif kind == "eubucco":
        validate_eubucco_source(source, job.params.source)
    elif kind not in {"nuts3", "floor_area", "building_age"}:
        raise ValueError(f"Unknown download source: {kind}")

    if source == partial:
        partial.replace(destination)


if __name__ == "__main__":
    sys.stderr = open(snakemake.log[0], "w")
    download(snakemake)
