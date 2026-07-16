from __future__ import annotations

from typer.testing import CliRunner

from retirement_engine.cli import app

runner = CliRunner()


def test_cli_help_builds_all_commands() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "benchmark" in result.output
    assert "validate-sources" in result.output


def test_run_rejects_invalid_as_of_date() -> None:
    result = runner.invoke(
        app,
        [
            "run",
            "--evidence",
            "data/benchmarks/evidence.csv",
            "--as-of",
            "not-a-date",
        ],
    )

    assert result.exit_code == 2
    assert "must be an ISO date" in result.output
