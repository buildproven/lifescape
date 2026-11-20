"""BUI-1804: the full suite runs in parallel across the available CPUs (REQ-TEST-1)."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import shlex
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
UV_LOCK = ROOT / "uv.lock"
TARGET = "tests/test_missing_data.py"


def _pyproject() -> dict[str, Any]:
    with PYPROJECT.open("rb") as handle:
        return tomllib.load(handle)


def _addopts() -> list[str]:
    return shlex.split(_pyproject()["tool"]["pytest"]["ini_options"]["addopts"])


def test_req_test_1_pytest_xdist_is_installed() -> None:
    assert importlib.util.find_spec("xdist") is not None
    assert importlib.metadata.version("pytest-xdist")


def test_req_test_1_pytest_xdist_is_a_locked_dev_dependency() -> None:
    dev = _pyproject()["project"]["optional-dependencies"]["dev"]
    assert any(requirement.startswith("pytest-xdist") for requirement in dev), dev
    with UV_LOCK.open("rb") as handle:
        lock = tomllib.load(handle)
    assert any(package["name"] == "pytest-xdist" for package in lock["package"])


def test_req_test_1_addopts_run_tests_across_available_cpus() -> None:
    args = _addopts()
    assert "-n" in args, args
    assert args[args.index("-n") + 1] == "auto", args


def test_req_test_1_distributed_verbose_run_names_each_test_file() -> None:
    """xdist prints only dots at net verbosity 0; Keel's `-v` must name every test (BUI-1490)."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-n", "2", "-v", TARGET],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"PASSED {TARGET}::" in result.stdout
