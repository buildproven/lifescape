"""BUI-1490: Keel's testCommand output must name every collected test file."""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
KEEL_JSON = ROOT / "keel.json"
PYPROJECT = ROOT / "pyproject.toml"
DESELECTING_FLAGS = ("-k", "-m", "--deselect", "--ignore", "--ignore-glob", "-x", "--exitfirst")


@pytest.fixture(scope="module")
def keel_config() -> dict[str, Any]:
    with KEEL_JSON.open(encoding="utf-8") as handle:
        config: dict[str, Any] = json.load(handle)
    return config


def _pytest_args(command: str) -> list[str]:
    tokens = shlex.split(command)
    assert "pytest" in tokens, f"testCommand must run pytest directly: {command!r}"
    return tokens[tokens.index("pytest") + 1 :]


def _verbosity(args: list[str]) -> int:
    level = 0
    for arg in args:
        if arg == "--verbose":
            level += 1
        elif arg == "--quiet":
            level -= 1
        elif arg.startswith("-") and not arg.startswith("--") and set(arg[1:]) <= {"v", "q"}:
            level += arg.count("v") - arg.count("q")
    return level


def _addopts() -> list[str]:
    with PYPROJECT.open("rb") as handle:
        pyproject = tomllib.load(handle)
    return shlex.split(pyproject["tool"]["pytest"]["ini_options"]["addopts"])


def test_req_keel_1_test_command_has_no_net_quiet(keel_config: dict[str, Any]) -> None:
    args = _pytest_args(keel_config["testCommand"])
    assert _verbosity(_addopts() + args) >= 0


def test_req_keel_1_test_command_output_names_test_files(keel_config: dict[str, Any]) -> None:
    args = _pytest_args(keel_config["testCommand"])
    target = "tests/test_missing_data.py"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", *args, "-p", "no:cacheprovider", target],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert target in result.stdout


def test_req_keel_2_test_command_runs_every_test(keel_config: dict[str, Any]) -> None:
    args = _pytest_args(keel_config["testCommand"])
    for flag in DESELECTING_FLAGS:
        assert not any(arg == flag or arg.startswith(f"{flag}=") for arg in args), flag
    assert not any(arg.startswith("tests/") or arg.endswith(".py") for arg in args)


def test_req_keel_2_checks_and_test_files_command_unchanged(
    keel_config: dict[str, Any],
) -> None:
    assert keel_config["checks"] == {
        "lint": "make lint",
        "test": "make test",
        "typecheck": "uv run --extra dev mypy src",
    }
    assert keel_config["testFilesCommand"] == "uv run pytest {files}"
