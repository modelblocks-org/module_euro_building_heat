"""Shared resumable transfers for cached source files."""

import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    snakemake: Any


def download(url: str, destination: str | Path) -> None:
    """Reuse cached files and resume partial transfers before renaming on success.

    Source validation belongs to processing. A completed but invalid download
    must be removed or replaced before retrying its processing job.
    """
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Preserve the format suffix so readers recognize compressed TSV and ZIP files.
    partial = destination.with_name(destination.stem + ".part" + destination.suffix)
    source = destination if destination.exists() else partial
    if source == partial:
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
    if source == partial:
        partial.replace(destination)


if __name__ == "__main__":
    sys.stderr = open(snakemake.log[0], "w")
    download(snakemake.params.url, snakemake.output[0])
