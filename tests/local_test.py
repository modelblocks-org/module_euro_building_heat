"""Local testing.

Should only be used sparingly, as it executes the full run of the module!
"""

import shutil
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


def clean_interface_default(
    default: str, *, shapes: str, scenario: str, year: int
) -> str:
    """Prune default paths in the interface file into useful string paths."""
    return (
        default.replace("<", "")
        .replace(">", "")
        .format(shapes=shapes, scenario=scenario, year=year)
    )


def build_full_result_request(interface: dict, shapes: str, scenarios: dict) -> str:
    """Request all configured scenario years and their report figures."""
    interface_results = interface["pathvars"]["results"]
    result_paths = [
        clean_interface_default(
            i["default"], shapes=shapes, scenario=scenario, year=year
        )
        for scenario, settings in scenarios.items()
        for year in settings["heat_demand"]
        for i in interface_results.values()
    ]
    result_paths.extend(
        f"resources/automatic/shapes/{shapes}/{scenario}/plots/{figure}_{year}.{extension}"
        for scenario, settings in scenarios.items()
        for year in settings["heat_demand"]
        for figure, extension in [
            ("space_heat_demand_density", "png"),
            ("annual_heat_demand", "png"),
            ("heat_demand_timeseries", "pdf"),
            ("heat_pump_cop_timeseries", "pdf"),
            ("heat_pump_electricity_demand_timeseries", "pdf"),
        ]
    )
    return " ".join(dict.fromkeys(result_paths))


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
    scenario_input = module_path / f"resources/user/{shapes}/scenario_input"
    scenario_input.mkdir(parents=True, exist_ok=True)
    scenario_file = scenario_input / "district_heating_shares.csv"
    if not scenario_file.exists():
        shutil.copyfile(module_path / "examples" / scenario_file.name, scenario_file)
    with (module_path / "config/config.yaml").open() as file:
        scenarios = yaml.safe_load(file)["scenarios"]
    request = build_full_result_request(interface, shapes, scenarios)

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
