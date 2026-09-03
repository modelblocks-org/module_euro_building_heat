"""Local testing.

Should only be used sparingly, as it executes the full run of the module!
"""

import subprocess
from pathlib import Path
from urllib.request import urlretrieve

import pytest
import yaml

URL = "https://zenodo.org/records/20765043/files/{shapes}.parquet?download=1"
SHAPES = ("EUROPE_S_C1_ADM1", "EUROPE_V_C4_NUTS1", "EUROPE_L_C34_ADM1")


def fetch_file(directory: Path, shapes: str) -> Path:
    """Grab a file for local testing."""
    file = directory / f"resources/user/{shapes}/shapes.parquet"

    if not file.exists():
        file.parent.mkdir(parents=True, exist_ok=True)

        url = URL.format(shapes=shapes)
        urlretrieve(url, file)

    return file


def clean_interface_default(default: str, *, shapes: str) -> str:
    """Prune default paths in the interface file into useful string paths."""
    return default.replace("<", "").replace(">", "").format(shapes=shapes)


def build_full_result_request(interface: dict, shapes: str) -> str:
    """Generate a string with all module results."""
    interface_results = interface["pathvars"]["results"]
    result_paths = [
        clean_interface_default(i["default"], shapes=shapes)
        for i in interface_results.values()
    ]
    return " ".join(result_paths)


@pytest.fixture
def interface(module_path) -> dict:
    """Fetch the interface file."""
    with open(module_path / "INTERFACE.yaml") as file:
        io_setup = yaml.safe_load(file)
    return io_setup


@pytest.mark.parametrize("shapes", SHAPES)
def test_full_run(module_path: Path, interface: dict, shapes: str):
    """A full run of the module, generating all outputs."""
    fetch_file(module_path, shapes)
    request = build_full_result_request(interface, shapes)

    assert subprocess.run(
        f"snakemake --use-conda --cores 4 --forceall --rerun-incomplete {request}",
        shell=True,
        check=True,
        cwd=module_path,
    )
    assert subprocess.run(
        f"snakemake --use-conda --cores 4 {request} --report results/{shapes}/report.html",
        shell=True,
        check=True,
        cwd=module_path,
    )
    assert subprocess.run(
        f"snakemake --use-conda --cores 4 {request} --rulegraph | dot -Tpng > results/{shapes}/rulegraph.png",
        shell=True,
        check=True,
        cwd=module_path,
    )
