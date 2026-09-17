"""Create standardised ECUK final energy demand baselines."""

import sys
from typing import TYPE_CHECKING, Any

import _ecuk
import _schemas

if TYPE_CHECKING:
    snakemake: Any


def main() -> None:
    """Create residential and services baselines."""
    raw_file = snakemake.input.raw_stats

    for sector in ["residential", "services"]:
        df = _ecuk.get_sector_demand(raw_file, sector)
        df = _schemas.BaselineSchema.validate_countries(df, ["GBR"])
        df.to_parquet(snakemake.output[sector], index=False)


if __name__ == "__main__":
    sys.stderr = open(snakemake.log[0], "w", buffering=1)
    main()
