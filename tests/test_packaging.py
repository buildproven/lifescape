from __future__ import annotations

import os
import subprocess
import zipfile
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
    with zipfile.ZipFile(wheel) as archive:
        packaged_files = set(archive.namelist())
    assert "lifescape/templates/landing.html" in packaged_files
    assert "lifescape/templates/demo.html" in packaged_files
    assert "lifescape/templates/app.html" in packaged_files
    assert "lifescape/static/landing.css" in packaged_files
    assert "lifescape/static/demo.css" in packaged_files
    assert "lifescape/static/landing.js" in packaged_files
    assert "lifescape/static/app.css" in packaged_files
    assert "lifescape/static/app.js" in packaged_files
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
            "lifescape",
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

    app_help = subprocess.run(
        [
            "uv",
            "run",
            "--isolated",
            "--with",
            str(wheel),
            "--",
            "lifescape",
            "app",
            "--help",
        ],
        cwd=outside_checkout,
        env=process_environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "guided local browser workspace" in app_help.stdout
