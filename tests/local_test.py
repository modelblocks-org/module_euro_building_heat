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


FIGURE_OUTPUTS = [
    "resources/automatic/shapes/{shapes}/plots/annual_heat_demand.png",
    "resources/automatic/shapes/{shapes}/plots/space_heat_demand_density.png",
    "resources/automatic/shapes/{shapes}/plots/heat_demand_timeseries.pdf",
    "resources/automatic/shapes/{shapes}/plots/heat_pump_cop_timeseries.pdf",
    "resources/automatic/shapes/{shapes}/plots/heat_pump_electricity_demand_timeseries.pdf",
    "resources/automatic/shapes/{shapes}/plots/building_count.png",
    "resources/automatic/shapes/{shapes}/plots/residential_space_heat_weight.png",
    "resources/automatic/shapes/{shapes}/plots/commercial_space_heat_weight.png",
    *[
        f"resources/automatic/baseline/{source}/{sector}.pdf"
        for source in ("jrc_idees", "che", "ecuk")
        for sector in ("residential", "services")
    ],
]


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


@pytest.mark.parametrize("shapes", SHAPES)
def test_report_figures(module_path: Path, shapes: str):
    """Generate diagnostic figures and an HTML report."""
    fetch_file(module_path, shapes)
    figures = [path.format(shapes=shapes) for path in FIGURE_OUTPUTS]
    subprocess.run(
        ["snakemake", "--use-conda", "--cores", "4", "--rerun-incomplete", *figures],
        check=True,
        cwd=module_path,
    )
    for figure in figures:
        path = module_path / figure
        assert path.is_file(), f"Missing figure: {figure}"
        assert path.stat().st_size > 0, f"Empty figure: {figure}"

    report = module_path / "results" / shapes / "figures-report.html"
    report.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["snakemake", "--use-conda", "--cores", "4", "--report", str(report), *figures],
        check=True,
        cwd=module_path,
    )
    assert report.is_file()
    assert report.stat().st_size > 0
