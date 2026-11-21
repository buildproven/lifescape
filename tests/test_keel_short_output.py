"""BUI-2026: Keel's testCommand output is short and still names every test file it runs."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
KEEL_JSON = ROOT / "keel.json"
FILES = ("tests/test_missing_data.py", "tests/test_gitignore.py")
BUDGET_FILE = "tests/test_release_budget_isolation.py"
BUDGET_TEST = f"{BUDGET_FILE}::test_release_budget_runs_outside_xdist_workers"


def _test_command_args() -> list[str]:
    with KEEL_JSON.open(encoding="utf-8") as handle:
        command: str = json.load(handle)["testCommand"]
    tokens = shlex.split(command)
    return tokens[tokens.index("pytest") + 1 :]


def _pytest(
    *args: str, cwd: Path = ROOT, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    base = {k: v for k, v in os.environ.items() if k != "PYTEST_XDIST_WORKER"}
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
        env={**base, **(env or {})},
    )


def _file_lines(stdout: str, prefix: str = "test files (") -> list[str]:
    return [line for line in stdout.splitlines() if line.startswith(prefix)]


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


def test_req_keel_3_test_command_does_not_print_a_line_per_test() -> None:
    # Net verbosity 1 prints one line per test; below 0 hides the test-file line Keel needs.
    assert _verbosity(_test_command_args()) == 0


@pytest.mark.parametrize("workers", ["2", "0"])
def test_req_keel_4_run_names_each_test_file_on_one_line(workers: str) -> None:
    result = _pytest(*_test_command_args(), "-n", workers, *FILES)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _file_lines(result.stdout) == [f"test files ({len(FILES)}): {' '.join(FILES)}"]


def test_req_keel_4_parallel_output_is_shorter_than_one_line_per_test() -> None:
    result = _pytest(*_test_command_args(), "-n", "2", *FILES)
    assert result.returncode == 0, result.stdout + result.stderr
    collected = _pytest("--collect-only", "-q", "-n", "0", *FILES).stdout
    tests = sum(1 for line in collected.splitlines() if "::" in line)
    assert tests > 0
    assert not any("PASSED" in line for line in result.stdout.splitlines()), result.stdout
    assert len(result.stdout.splitlines()) < tests + 10, result.stdout


def test_req_keel_4_empty_selection_prints_no_file_line() -> None:
    result = _pytest("-n", "2", "-k", "no_test_has_this_name", *FILES)
    assert result.returncode == pytest.ExitCode.NO_TESTS_COLLECTED, result.stdout + result.stderr
    assert _file_lines(result.stdout) == []


def test_req_keel_4_nested_controller_ignores_inherited_worker_environment() -> None:
    result = _pytest(
        *_test_command_args(),
        "-n",
        "0",
        *FILES,
        env={"PYTEST_XDIST_WORKER": "gw0"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert _file_lines(result.stdout) == [f"test files ({len(FILES)}): {' '.join(FILES)}"]


def test_req_keel_5_failure_details_follow_the_file_line(tmp_path: Path) -> None:
    # Keel reads the tail of the output, so the file list must not push failure details out of it.
    (tmp_path / "test_fails.py").write_text(
        "def test_fails():\n    assert 'left' == 'BUI_2026_FAILURE_MARKER'\n", encoding="utf-8"
    )
    result = _pytest(
        "-o",
        "addopts=",
        "-n",
        "2",
        "-p",
        "conftest",
        "test_fails.py",
        cwd=tmp_path,
        env={"PYTHONPATH": str(ROOT / "tests")},
    )
    assert result.returncode == pytest.ExitCode.TESTS_FAILED, result.stdout + result.stderr
    file_line = _file_lines(result.stdout)
    assert file_line == ["test files (1): test_fails.py"], result.stdout
    assert result.stdout.index(file_line[0]) < result.stdout.index("BUI_2026_FAILURE_MARKER")


def test_req_keel_6_serial_budget_pass_names_its_test_files() -> None:
    # Workers deselect budget tests, so the serial pass names a file holding only budget tests.
    result = _pytest("-n", "2", BUDGET_TEST)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _file_lines(result.stdout, "release-budget test files (") == [
        f"release-budget test files (1): {BUDGET_FILE}"
    ], result.stdout
