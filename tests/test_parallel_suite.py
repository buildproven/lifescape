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

import pytest

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
UV_LOCK = ROOT / "uv.lock"
TARGET = "tests/test_missing_data.py"
SECTION = " test files "


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


def _run(*args: str) -> str:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *args, TARGET],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def test_req_test_1_distributed_quiet_run_names_each_test_file() -> None:
    output = _run("-n", "2")
    assert SECTION in output
    section = output.split(SECTION, 1)[1]
    assert f"{TARGET} " in section
    assert " passed" in section


# addopts carry -q, so one -v is net verbosity 0 and -vv is net 1.
@pytest.mark.parametrize("args", [("-n", "0", "-v"), ("-n", "2", "-vv")])
def test_req_test_1_file_summary_only_when_progress_omits_file_names(
    args: tuple[str, ...],
) -> None:
    output = _run(*args)
    assert SECTION not in output
    assert TARGET in output
