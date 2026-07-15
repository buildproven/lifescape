"""Command-line interface for the Milestone 1 vertical slice."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from retirement_engine.config import load_sources
from retirement_engine.evidence import SourcePolicyError, validate_source
from retirement_engine.models import Confidence, SourceRecord, SourceTier
from retirement_engine.pipeline import execute_run

app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)


def _event(event: str, **fields: object) -> None:
    typer.echo(json.dumps({"event": event, **fields}, sort_keys=True), err=True)


@app.command("run")
def run_command(
    evidence: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
    config_dir: Annotated[Path, typer.Option(exists=True, file_okay=False)] = Path("config"),
    database: Annotated[Path, typer.Option()] = Path("outputs/run.sqlite"),
    output_dir: Annotated[Path, typer.Option()] = Path("outputs/run"),
    profile: Annotated[Path | None, typer.Option(exists=True, dir_okay=False)] = None,
    simulations: Annotated[int, typer.Option(min=1000)] = 1000,
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
    )
    _event(
        "run_completed",
        run_id=result.run_id,
        eligible=len(result.scores),
        total_places=len(result.places),
        output_dir=str(output_dir),
    )
    typer.echo(result.run_id)


@app.command()
def benchmark(
    output_dir: Annotated[Path, typer.Option()] = Path("outputs/benchmark"),
    config_dir: Annotated[Path, typer.Option(exists=True, file_okay=False)] = Path("config"),
    simulations: Annotated[int, typer.Option(min=1000)] = 1000,
) -> None:
    """Run the ten-town, explicitly synthetic golden benchmark."""
    evidence = Path("data/benchmarks/evidence.csv")
    database = output_dir / "benchmark.sqlite"
    _event("benchmark_started", towns=10, synthetic=True)
    result = execute_run(
        evidence_path=evidence,
        config_dir=config_dir,
        database_path=database,
        output_dir=output_dir,
        simulations=simulations,
    )
    _event(
        "benchmark_completed",
        run_id=result.run_id,
        eligible=len(result.scores),
        blocked=len(result.places) - len(result.scores),
    )
    typer.echo(result.run_id)


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
