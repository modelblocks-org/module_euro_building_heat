"""Shared pytest fixtures."""

import os
import shutil
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def module_path():
    """Parent directory of the project."""
    path = Path(__file__).parent.parent

    # Ensure the EDH API is set up.
    edh_api = path / "resources/user/edh_api.txt"
    integration_api = path / "tests/integration/resources/user/edh_api.txt"
    integration_api.parent.mkdir(parents=True, exist_ok=True)
    if edh_api.is_file():
        shutil.copyfile(edh_api, integration_api)
    elif api_key := os.environ.get("EDH_API_KEY"):
        Path(integration_api).write_text(api_key.strip(), encoding="utf-8")
    else:
        raise RuntimeError("No EDH API set up for integration testing.")
    return path
