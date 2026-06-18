"""Extract one member from a zip archive."""

from pathlib import Path
from zipfile import ZipFile

output_path = Path(snakemake.output[0])
output_path.parent.mkdir(parents=True, exist_ok=True)
with ZipFile(snakemake.input[0]) as archive:
    output_path.write_bytes(archive.read(snakemake.params.member))
