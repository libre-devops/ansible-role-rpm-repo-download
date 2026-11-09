"""Load the role's module from library/ so the tests can call its helpers directly."""

import importlib.util
import pathlib

import pytest

MODULE_PATH = pathlib.Path(__file__).resolve().parents[2] / "library" / "rpm_repo_query.py"


@pytest.fixture(scope="session")
def rrq():
    spec = importlib.util.spec_from_file_location("rpm_repo_query", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
