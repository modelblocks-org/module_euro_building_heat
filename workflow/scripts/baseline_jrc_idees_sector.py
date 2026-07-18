"""Process JRC-IDEES tertiary sector heat demand data."""

import sys
from typing import TYPE_CHECKING, Any

import _jrc
import _plots
import _schemas
import _utils
import pandas as pd

if TYPE_CHECKING:
    snakemake: Any

SECTOR_MAP = {"residential": "RES", "services": "SER"}


def get_combined_sector_demand(
    jrc_files: list[str], sector: str, energy_type: str, countries: list[str]
) -> pd.DataFrame:
    """Get a validated sector demand dataset."""
    df = _jrc.get_sector_data(jrc_files, SECTOR_MAP[sector], energy_type)

    # FIXME: data in settings.yaml should be in alpha3 already
    country_translator = {i: _utils.convert_country_code(i, "alpha3") for i in countries}
    df["country_code"] = df["country_code"].map(country_translator)
    df["sector"] = sector
    df = _schemas.JRCIDEESSchema.validate_countries(df, country_translator.values())
    return df


def main() -> None:
    """Main snakemake process."""
    jrc_files = list(snakemake.input.jrc_files)

    countries: list[str] = snakemake.params.countries
    sector: str = snakemake.wildcards.sector

    final_df = get_combined_sector_demand(jrc_files, sector, "final_energy", countries)
    final_df.to_csv(snakemake.output.final, index=False)

    useful_df = get_combined_sector_demand(
        jrc_files, sector, "useful_energy", countries
    )
    useful_df.to_csv(snakemake.output.useful, index=False)

    fig, axes = _plots.plot_bar_histogram(
        final_df, "end_use", container_col="country_code", format_container=False
    )
    _plots.plot_value_histogram(
        useful_df,
        container_col="country_code",
        label="useful_energy",
        fig=fig,
        axes=axes,
        unit="ktoe",
    )
    fig.suptitle(f"{sector.capitalize()} energy demand")
    fig.savefig(snakemake.output.plot, bbox_inches="tight")


if __name__ == "__main__":
    sys.stderr = open(snakemake.log[0], "w", buffering=1)
    main()
