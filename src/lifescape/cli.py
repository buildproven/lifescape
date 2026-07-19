"""Command-line interface for the Milestone 1 vertical slice."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Annotated

import typer
import yaml

from lifescape.config import ConfigurationError, load_sources
from lifescape.connectors.census_acs import CensusAcsConnector
from lifescape.connectors.orchestrate import PlaceRequest, fetch_live_observations
from lifescape.evidence import SourcePolicyError, validate_source
from lifescape.models import Confidence, SourceRecord, SourceTier
from lifescape.pipeline import execute_run
from lifescape.resources import bundled_benchmark

app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)


def _event(event: str, **fields: object) -> None:
    typer.echo(json.dumps({"event": event, **fields}, sort_keys=True), err=True)


def _parse_optional_date(value: str | None, option_name: str) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise typer.BadParameter(
            "must be an ISO date (YYYY-MM-DD)", param_hint=option_name
        ) from exc


@app.command("run")
def run_command(
    evidence: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
    config_dir: Annotated[Path, typer.Option(exists=True, file_okay=False)] = Path("config"),
    database: Annotated[Path, typer.Option()] = Path("outputs/run.sqlite"),
    output_dir: Annotated[Path, typer.Option()] = Path("outputs/run"),
    profile: Annotated[Path | None, typer.Option(exists=True, dir_okay=False)] = None,
    simulations: Annotated[int, typer.Option(min=1000)] = 1000,
    sensitivity_seed: Annotated[int, typer.Option()] = 20260714,
    as_of: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Evaluate a manual evidence CSV and write comparison reports."""
    _event("run_started", evidence=str(evidence), config_dir=str(config_dir))
    result = execute_run(
        evidence_path=evidence,
        config_dir=config_dir,
        database_path=database,
        output_dir=output_dir,
        profile_path=profile,
        simulations=simulations,
        sensitivity_seed=sensitivity_seed,
        as_of=_parse_optional_date(as_of, "--as-of"),
    )
    _event(
        "run_completed",
        run_id=result.run_id,
        eligible=len(result.scores),
        total_places=len(result.places),
        persisted=result.persisted,
        output_dir=str(output_dir),
    )
    typer.echo(result.run_id)


def _load_place_requests(path: Path) -> tuple[PlaceRequest, ...]:
    """Load a {place_id: "state_fips:place_fips"} mapping used to fetch live evidence."""
    try:
        with path.open(encoding="utf-8") as handle:
            raw = yaml.safe_load(handle)
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigurationError(f"cannot load {path}: {exc}") from exc
    if not isinstance(raw, dict) or not raw:
        raise ConfigurationError(f"{path} must map place_id to a 'state_fips:place_fips' string")
    return tuple(
        PlaceRequest(place_id=place_id, geography=geography)
        for place_id, geography in sorted(raw.items())
    )


@app.command("live-run")
def live_run_command(
    places: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
    evidence: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
    config_dir: Annotated[Path, typer.Option(exists=True, file_okay=False)] = Path("config"),
    research_brief: Annotated[Path, typer.Option(exists=True, dir_okay=False)] = Path(
        "config/research_brief.live.yaml"
    ),
    database: Annotated[Path, typer.Option()] = Path("outputs/live-run.sqlite"),
    output_dir: Annotated[Path, typer.Option()] = Path("outputs/live-run"),
    profile: Annotated[Path | None, typer.Option(exists=True, dir_okay=False)] = None,
    simulations: Annotated[int, typer.Option(min=1000)] = 1000,
    sensitivity_seed: Annotated[int, typer.Option()] = 20260714,
    as_of: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Fetch live connector evidence, merge it with a manual CSV, and write reports.

    --evidence may point to a headers-only CSV for a purely connector-driven run;
    manual rows always take precedence over live-fetched values for the same
    (place, metric). A connector failure for a place degrades that metric to
    UNKNOWN evidence rather than aborting the run.
    """
    place_requests = _load_place_requests(places)
    connectors = [CensusAcsConnector()]
    _event(
        "live_run_started",
        places=len(place_requests),
        connectors=[connector.name for connector in connectors],
    )
    live_observations = fetch_live_observations(
        connectors,
        place_requests,
        on_event=lambda event, fields: _event(event, **fields),
    )
    result = execute_run(
        evidence_path=evidence,
        config_dir=config_dir,
        research_brief_path=research_brief,
        database_path=database,
        output_dir=output_dir,
        profile_path=profile,
        simulations=simulations,
        sensitivity_seed=sensitivity_seed,
        as_of=_parse_optional_date(as_of, "--as-of"),
        live_observations=live_observations,
    )
    _event(
        "live_run_completed",
        run_id=result.run_id,
        eligible=len(result.scores),
        total_places=len(result.places),
        live_observations=len(live_observations),
        persisted=result.persisted,
        output_dir=str(output_dir),
    )
    typer.echo(result.run_id)


@app.command()
def benchmark(
    output_dir: Annotated[Path, typer.Option()] = Path("outputs/benchmark"),
    config_dir: Annotated[Path | None, typer.Option(exists=True, file_okay=False)] = None,
    simulations: Annotated[int, typer.Option(min=1000)] = 1000,
    sensitivity_seed: Annotated[int, typer.Option()] = 20260714,
) -> None:
    """Run the ten-town, explicitly synthetic golden benchmark."""
    database = output_dir / "benchmark.sqlite"
    _event("benchmark_started", towns=10, synthetic=True)
    with bundled_benchmark() as (evidence, packaged_config):
        effective_config = config_dir or packaged_config
        result = execute_run(
            evidence_path=evidence,
            config_dir=effective_config,
            database_path=database,
            output_dir=output_dir,
            simulations=simulations,
            sensitivity_seed=sensitivity_seed,
        )
    _event(
        "benchmark_completed",
        run_id=result.run_id,
        eligible=len(result.scores),
        blocked=len(result.places) - len(result.scores),
    )
    typer.echo(result.run_id)


@app.command("app")
def app_command(
    port: Annotated[int, typer.Option(min=1024, max=65535)] = 8765,
    output_dir: Annotated[Path, typer.Option()] = Path("outputs/app"),
    no_open: Annotated[bool, typer.Option("--no-open")] = False,
) -> None:
    """Open the guided local browser workspace."""
    from lifescape.web import serve

    _event("app_started", url=f"http://127.0.0.1:{port}", output_dir=str(output_dir))
    serve(port=port, output_dir=output_dir, open_browser=not no_open)


@app.command("validate-sources")
def validate_sources(
    tier: Annotated[SourceTier, typer.Option()],
    confidence: Annotated[Confidence, typer.Option()] = Confidence.HIGH,
    config_dir: Annotated[Path, typer.Option(exists=True, file_okay=False)] = Path("config"),
) -> None:
    """Validate a source classification against the scoring policy."""
    from datetime import date

    source = SourceRecord(
        url="manual://validation",
        title="Manual source validation",
        publisher="operator",
        tier=tier,
        retrieved_at=date.today(),
        geography="town",
        confidence=confidence,
    )
    try:
        validate_source(source, load_sources(config_dir))
    except SourcePolicyError as exc:
        _event("source_rejected", reason=str(exc), tier=tier)
        raise typer.Exit(code=2) from exc
    _event("source_accepted", tier=tier, confidence=confidence)


if __name__ == "__main__":
    app()
