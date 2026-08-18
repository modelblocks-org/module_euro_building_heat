"""Create standardised ECUK final energy demand baselines."""

import sys
from typing import TYPE_CHECKING, Any

import _ecuk
import _plots
import _schemas

if TYPE_CHECKING:
    snakemake: Any


def main() -> None:
    """Create and plot residential and services baselines."""
    raw_file = snakemake.input.raw_stats

    for sector in ["residential", "services"]:
        df = _ecuk.get_sector_demand(raw_file, sector)
        df = _schemas.BaselineSchema.validate_countries(df, ["GBR"])
        df.to_csv(snakemake.output[sector], index=False)

        fig, _ = _plots.plot_bar_histogram(
            df, "end_use", container_col="country_code", unit="TWh"
        )
        fig.suptitle(f"{sector.capitalize()} final energy demand")
        fig.savefig(snakemake.output[f"{sector}_plot"], bbox_inches="tight")


if __name__ == "__main__":
    sys.stderr = open(snakemake.log[0], "w", buffering=1)
    main()
