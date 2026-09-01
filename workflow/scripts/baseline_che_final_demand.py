"""Create standardised CHE final energy demand baselines."""

import sys
from typing import TYPE_CHECKING, Any

import _che
import _plots
import _schemas

if TYPE_CHECKING:
    snakemake: Any


def main() -> None:
    """Main snakemake process."""
    raw_file = snakemake.input.raw_stats

    residential_df = _che.get_residential_demand(raw_file)
    services_df = _che.get_services_demand(raw_file, residential_df)

    for df in [residential_df, services_df]:
        df = _schemas.BaselineSchema.validate(df)
        sector = df["sector"].iat[0]
        df.to_parquet(snakemake.output[sector], index=False)
        fig, _ = _plots.plot_bar_histogram(
            df, "end_use", container_col="country_code", unit="TWh"
        )
        fig.suptitle(f"{sector.capitalize()} final energy demand")
        fig.savefig(snakemake.output[f"{sector}_plot"], bbox_inches="tight")


if __name__ == "__main__":
    sys.stderr = open(snakemake.log[0], "w", buffering=1)
    main()
