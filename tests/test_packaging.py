from __future__ import annotations

import os
import subprocess
from pathlib import Path


def test_installed_wheel_runs_benchmark_outside_checkout(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[1]
    distribution = tmp_path / "dist"
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(distribution)],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    wheel = next(distribution.glob("*.whl"))
    outside_checkout = tmp_path / "elsewhere"
    outside_checkout.mkdir()
    output_dir = outside_checkout / "output"
    process_environment = os.environ.copy()
    process_environment.pop("PYTHONPATH", None)
    completed = subprocess.run(
        [
            "uv",
            "run",
            "--isolated",
            "--with",
            str(wheel),
            "--",
            "retire",
            "benchmark",
            "--output-dir",
            str(output_dir),
        ],
        cwd=outside_checkout,
        env=process_environment,
        check=True,
        capture_output=True,
        text=True,
    )

    assert completed.stdout.strip()
    assert (output_dir / "comparison.md").is_file()
    assert (output_dir / "benchmark.sqlite").is_file()
