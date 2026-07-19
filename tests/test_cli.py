from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.core import TyperOption
from typer.main import get_command
from typer.testing import CliRunner

from lifescape.cli import app

runner = CliRunner()

CENSUS_PAYLOAD: list[list[str]] = [
    ["NAME", "DP02_0068PE", "state", "place"],
    ["Lake Geneva city, Wisconsin", "42.1", "55", "43075"],
]

EVIDENCE_HEADER = (
    "place_id,place_name,state,geography_type,source_url,source_title,publisher,tier,"
    "retrieved_at,observed_period,observed_at,source_geography,confidence,synthetic\n"
)


def _mock_urlopen_response(payload: list[list[str]]) -> MagicMock:
    handle = MagicMock()
    handle.read.return_value = json.dumps(payload).encode()
    handle.__enter__.return_value = handle
    handle.__exit__.return_value = False
    return handle


def test_cli_help_builds_all_commands() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "benchmark" in result.output
    assert "app" in result.output
    assert "validate-sources" in result.output


def test_app_command_help_exposes_local_options() -> None:
    result = runner.invoke(app, ["app", "--help"])
    command = get_command(app).commands["app"]
    option_flags = {
        flag
        for parameter in command.params
        if isinstance(parameter, TyperOption)
        for flag in parameter.opts
    }

    assert result.exit_code == 0
    assert {"--port", "--output-dir", "--no-open"} <= option_flags


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


def test_live_run_fetches_merges_and_writes_reports(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CENSUS_API_KEY", "test-key")
    places = tmp_path / "places.yaml"
    places.write_text('lake_geneva_wi: "55:43075"\n', encoding="utf-8")
    evidence = tmp_path / "evidence.csv"
    evidence.write_text(EVIDENCE_HEADER, encoding="utf-8")
    output_dir = tmp_path / "output"

    with patch(
        "lifescape.connectors.census_acs.urlopen",
        return_value=_mock_urlopen_response(CENSUS_PAYLOAD),
    ):
        result = runner.invoke(
            app,
            [
                "live-run",
                "--places",
                str(places),
                "--evidence",
                str(evidence),
                "--database",
                str(tmp_path / "live.sqlite"),
                "--output-dir",
                str(output_dir),
            ],
        )

    assert result.exit_code == 0, result.output
    events = [json.loads(line) for line in result.output.splitlines() if line.startswith("{")]
    completed = next(event for event in events if event["event"] == "live_run_completed")
    assert completed["live_observations"] == 1
    assert completed["total_places"] == 1
    assert (output_dir / "comparison.md").exists()


def test_live_run_reports_connector_failures_without_aborting(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CENSUS_API_KEY", "test-key")
    places = tmp_path / "places.yaml"
    places.write_text('broken_town: "not-a-real-geography"\n', encoding="utf-8")
    evidence = tmp_path / "evidence.csv"
    evidence.write_text(EVIDENCE_HEADER, encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "live-run",
            "--places",
            str(places),
            "--evidence",
            str(evidence),
            "--database",
            str(tmp_path / "live.sqlite"),
            "--output-dir",
            str(tmp_path / "output"),
        ],
    )

    assert result.exit_code != 0
    assert "connector_fetch_failed" in result.output
    assert result.exception is not None
    assert "evidence produced no observations" in str(result.exception)


def test_live_run_rejects_empty_places_file(tmp_path: Path) -> None:
    places = tmp_path / "places.yaml"
    places.write_text("{}\n", encoding="utf-8")
    evidence = tmp_path / "evidence.csv"
    evidence.write_text(EVIDENCE_HEADER, encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "live-run",
            "--places",
            str(places),
            "--evidence",
            str(evidence),
            "--database",
            str(tmp_path / "live.sqlite"),
            "--output-dir",
            str(tmp_path / "output"),
        ],
    )

    assert result.exit_code != 0
    assert result.exception is not None
    assert "must map place_id" in str(result.exception)


def test_live_run_rejects_malformed_places_yaml(tmp_path: Path) -> None:
    places = tmp_path / "places.yaml"
    places.write_text("- not\n- a\n- mapping\n", encoding="utf-8")
    evidence = tmp_path / "evidence.csv"
    evidence.write_text(EVIDENCE_HEADER, encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "live-run",
            "--places",
            str(places),
            "--evidence",
            str(evidence),
            "--database",
            str(tmp_path / "live.sqlite"),
            "--output-dir",
            str(tmp_path / "output"),
        ],
    )

    assert result.exit_code != 0
    assert result.exception is not None
    assert "must map place_id" in str(result.exception)


def test_live_run_manual_evidence_takes_precedence_over_live(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CENSUS_API_KEY", "test-key")
    places = tmp_path / "places.yaml"
    places.write_text('lake_geneva_wi: "55:43075"\n', encoding="utf-8")
    evidence = tmp_path / "evidence.csv"
    evidence.write_text(
        EVIDENCE_HEADER.rstrip("\n") + ",education_attainment\n"
        "lake_geneva_wi,Lake Geneva,WI,town,https://example.gov,Manual survey,Operator,A,"
        "2026-01-01,2025,2025-12-31,town,high,true,55.0\n",
        encoding="utf-8",
    )
    output_dir = tmp_path / "output"

    with patch(
        "lifescape.connectors.census_acs.urlopen",
        return_value=_mock_urlopen_response(CENSUS_PAYLOAD),
    ):
        result = runner.invoke(
            app,
            [
                "live-run",
                "--places",
                str(places),
                "--evidence",
                str(evidence),
                "--database",
                str(tmp_path / "live.sqlite"),
                "--output-dir",
                str(output_dir),
            ],
        )

    assert result.exit_code == 0, result.output
    report = (output_dir / "comparison.md").read_text(encoding="utf-8")
    assert "55.0" in report
    assert "42.1" not in report


def test_live_run_rejects_place_identity_conflict_between_manual_and_live(
    tmp_path: Path, monkeypatch
) -> None:
    """A live connector's derived place name/state must not silently overwrite a manual entry."""
    monkeypatch.setenv("CENSUS_API_KEY", "test-key")
    places = tmp_path / "places.yaml"
    places.write_text('lake_geneva_wi: "55:43075"\n', encoding="utf-8")
    evidence = tmp_path / "evidence.csv"
    evidence.write_text(
        EVIDENCE_HEADER.rstrip("\n") + ",median_sale_price\n"
        "lake_geneva_wi,Lake Geneva,WI,town,https://example.gov,Manual survey,Operator,A,"
        "2026-01-01,2025,2025-12-31,town,high,true,500000\n",
        encoding="utf-8",
    )

    with patch(
        "lifescape.connectors.census_acs.urlopen",
        return_value=_mock_urlopen_response(CENSUS_PAYLOAD),
    ):
        result = runner.invoke(
            app,
            [
                "live-run",
                "--places",
                str(places),
                "--evidence",
                str(evidence),
                "--database",
                str(tmp_path / "live.sqlite"),
                "--output-dir",
                str(tmp_path / "output"),
            ],
        )

    assert result.exit_code != 0
    assert result.exception is not None
    assert "inconsistent identity for place" in str(result.exception)
