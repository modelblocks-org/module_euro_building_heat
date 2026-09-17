"""Record figures requested by the optional aggregate report target."""

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    snakemake: Any

Path(snakemake.output[0]).write_text("\n".join(snakemake.input) + "\n")
