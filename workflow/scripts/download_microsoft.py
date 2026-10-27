"""Resolve and download a Microsoft building tile part."""

import sys
from typing import TYPE_CHECKING, Any

import pandas as pd
from _schemas import MicrosoftIndexSchema
from download import download

if TYPE_CHECKING:
    snakemake: Any

sys.stderr = open(snakemake.log[0], "w")


links = MicrosoftIndexSchema.validate(
    pd.read_csv(snakemake.input.index, dtype={"QuadKey": str})
)
urls = sorted(links.loc[links.QuadKey.eq(snakemake.wildcards.quadkey), "Url"])
url = urls[int(snakemake.wildcards.part)]
download(url, snakemake.output.table)
