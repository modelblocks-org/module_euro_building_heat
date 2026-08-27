"""Extract the single GeoJSON member from timezone-boundary-builder."""

import sys
from pathlib import Path, PurePosixPath
from zipfile import ZipFile


def extract_timezone_geojson(archive_path: str | Path, output_path: str | Path) -> None:
    """Extract exactly one GeoJSON-like member using an atomic output rename."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(output_path.suffix + ".tmp")

    with ZipFile(archive_path) as archive:
        candidates = [
            member
            for member in archive.infolist()
            if not member.is_dir()
            and PurePosixPath(member.filename).suffix.lower() in {".geojson", ".json"}
        ]
        if len(candidates) != 1:
            names = [member.filename for member in candidates]
            raise ValueError(
                "Expected exactly one GeoJSON member in timezone boundary archive, "
                f"found {len(candidates)}: {names}"
            )

        with archive.open(candidates[0]) as source, temporary_path.open("wb") as target:
            while chunk := source.read(1024 * 1024):
                target.write(chunk)

    temporary_path.replace(output_path)


if __name__ == "__main__":
    sys.stderr = open(snakemake.log[0], "w", buffering=1)
    extract_timezone_geojson(
        snakemake.input.archive,
        snakemake.output.geojson,
    )
