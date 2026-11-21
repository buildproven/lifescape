"""BUI-2024: CLAUDE.md describes the parallel pytest-xdist suite, and its claims hold."""

from __future__ import annotations

import shlex
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLAUDE_MD = ROOT / "CLAUDE.md"
PYPROJECT = ROOT / "pyproject.toml"
TARGET = "tests/test_gates.py"


def _commands_section() -> str:
    text = CLAUDE_MD.read_text(encoding="utf-8")
    start = text.index("## Commands")
    end = text.index("\n## ", start + 1)
    return text[start:end]


def _addopts() -> list[str]:
    with PYPROJECT.open("rb") as handle:
        config = tomllib.load(handle)
    return shlex.split(config["tool"]["pytest"]["ini_options"]["addopts"])


def _pytest(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_commands_say_the_suite_runs_with_n_auto_from_addopts() -> None:
    section = _commands_section()
    assert "pytest-xdist" in section
    assert "`-n auto`" in section
    assert "`addopts`" in section and "`pyproject.toml`" in section
    args = _addopts()
    assert args[args.index("-n") + 1] == "auto", args


def test_commands_no_longer_give_a_serial_single_test_command() -> None:
    section = _commands_section()
    assert "single test: `.venv/bin/pytest tests/" not in section
    assert "`.venv/bin/pytest -n 0 tests/test_gates.py::test_name`" in section


def test_documented_single_test_command_with_n_0_passes() -> None:
    result = _pytest("-n", "0", "-q", TARGET)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "passed" in result.stdout


def test_documented_p_no_xdist_failure_matches_pytest() -> None:
    section = _commands_section()
    assert "`-p no:xdist`" in section
    assert "`unrecognized arguments: -n`" in section
    result = _pytest("-p", "no:xdist", "-q", TARGET)
    assert result.returncode == 4, result.stdout + result.stderr
    assert "unrecognized arguments: -n" in result.stderr
