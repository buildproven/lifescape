"""Release-budget tests run alone, never on a parallel worker (BUI-1804)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_release_budget_runs_outside_xdist_workers() -> None:
    # The name makes conftest treat this as a release-budget test.
    assert "PYTEST_XDIST_WORKER" not in os.environ


def test_parallel_run_reports_the_serial_budget_pass() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "-n",
            "2",
            # Only the budget test: running this whole file here would recurse.
            f"{Path(__file__).relative_to(ROOT)}::test_release_budget_runs_outside_xdist_workers",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
        env={k: v for k, v in os.environ.items() if k != "PYTEST_XDIST_WORKER"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "release-budget tests run serially: 1 passed" in result.stdout, result.stdout
