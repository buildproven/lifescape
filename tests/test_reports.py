import csv
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
import yaml

from retirement_engine.db import (
    GateResultRow,
    MetricRow,
    ResearchRunRow,
    SensitivityRow,
    SourceRow,
    initialize_database,
)
from retirement_engine.pipeline import execute_run


def test_benchmark_vertical_slice_is_reproducible(tmp_path: Path) -> None:
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first = execute_run(
        evidence_path=Path("data/benchmarks/evidence.csv"),
        config_dir=Path("config"),
        database_path=first_dir / "run.sqlite",
        output_dir=first_dir,
    )
    second = execute_run(
        evidence_path=Path("data/benchmarks/evidence.csv"),
        config_dir=Path("config"),
        database_path=second_dir / "run.sqlite",
        output_dir=second_dir,
    )
    assert first == second
    assert len(first.places) == 10
    assert len(first.scores) == 5
    for name in ("comparison.md", "comparison.csv", "sensitivity.csv"):
        assert (first_dir / name).read_bytes() == (second_dir / name).read_bytes()
    report = (first_dir / "comparison.md").read_text(encoding="utf-8")
    assert "Synthetic benchmark warning" in report
    assert "UNKNOWN" in report


def test_distinct_profiles_persist_as_separate_runs_in_one_database(tmp_path: Path) -> None:
    database = tmp_path / "runs.sqlite"
    first = execute_run(
        evidence_path=Path("data/benchmarks/evidence.csv"),
        config_dir=Path("config"),
        database_path=database,
        output_dir=tmp_path / "first",
    )
    profile = yaml.safe_load(Path("config/user_profile.example.yaml").read_text(encoding="utf-8"))
    profile["profile_version"] = "2.0"
    profile_path = tmp_path / "profile.yaml"
    profile_path.write_text(yaml.safe_dump(profile), encoding="utf-8")
    second = execute_run(
        evidence_path=Path("data/benchmarks/evidence.csv"),
        config_dir=Path("config"),
        profile_path=profile_path,
        database_path=database,
        output_dir=tmp_path / "second",
    )

    session, engine = initialize_database(database)
    try:
        assert session.query(ResearchRunRow).count() == 2
        assert session.query(MetricRow).count() == 34
        assert session.query(SensitivityRow).count() == 10
        assert session.query(GateResultRow).filter(GateResultRow.source_id.is_not(None)).count() > 0
        assert session.connection().exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
    finally:
        session.close()
        engine.dispose()
    assert first.run_id != second.run_id


def test_identical_run_reports_idempotent_persistence(tmp_path: Path) -> None:
    database = tmp_path / "runs.sqlite"
    first = execute_run(
        evidence_path=Path("data/benchmarks/evidence.csv"),
        config_dir=Path("config"),
        database_path=database,
        output_dir=tmp_path / "first",
    )
    second = execute_run(
        evidence_path=Path("data/benchmarks/evidence.csv"),
        config_dir=Path("config"),
        database_path=database,
        output_dir=tmp_path / "second",
    )

    assert first.persisted is True
    assert second.persisted is False


def test_concurrent_identical_runs_are_idempotent(tmp_path: Path) -> None:
    database = tmp_path / "runs.sqlite"
    barrier = Barrier(4)

    def run(index: int) -> bool:
        barrier.wait()
        return execute_run(
            evidence_path=Path("data/benchmarks/evidence.csv"),
            config_dir=Path("config"),
            database_path=database,
            output_dir=tmp_path / f"same-{index}",
        ).persisted

    with ThreadPoolExecutor(max_workers=4) as pool:
        persisted = list(pool.map(run, range(4)))

    assert persisted.count(True) == 1
    assert persisted.count(False) == 3


def test_concurrent_distinct_runs_share_places_safely(tmp_path: Path) -> None:
    database = tmp_path / "runs.sqlite"
    barrier = Barrier(4)

    def run(index: int) -> str:
        barrier.wait()
        result = execute_run(
            evidence_path=Path("data/benchmarks/evidence.csv"),
            config_dir=Path("config"),
            database_path=database,
            output_dir=tmp_path / f"distinct-{index}",
            sensitivity_seed=20260714 + index,
        )
        assert result.persisted is True
        return result.run_id

    with ThreadPoolExecutor(max_workers=4) as pool:
        run_ids = list(pool.map(run, range(4)))

    assert len(set(run_ids)) == 4
    session, engine = initialize_database(database)
    try:
        assert session.query(ResearchRunRow).count() == 4
    finally:
        session.close()
        engine.dispose()


def test_evidence_change_changes_run_identity(tmp_path: Path) -> None:
    evidence = tmp_path / "changed.csv"
    benchmark = Path("data/benchmarks/evidence.csv").read_text(encoding="utf-8")
    evidence.write_text(benchmark.replace(",510000,12,", ",500000,12,"), encoding="utf-8")
    first = execute_run(
        evidence_path=Path("data/benchmarks/evidence.csv"),
        config_dir=Path("config"),
        database_path=tmp_path / "first.sqlite",
        output_dir=tmp_path / "first",
    )
    second = execute_run(
        evidence_path=evidence,
        config_dir=Path("config"),
        database_path=tmp_path / "second.sqlite",
        output_dir=tmp_path / "second",
    )

    assert first.run_id != second.run_id


def test_place_identity_change_is_rejected_across_runs(tmp_path: Path) -> None:
    database = tmp_path / "runs.sqlite"
    execute_run(
        evidence_path=Path("data/benchmarks/evidence.csv"),
        config_dir=Path("config"),
        database_path=database,
        output_dir=tmp_path / "first",
    )
    evidence = tmp_path / "renamed.csv"
    benchmark = Path("data/benchmarks/evidence.csv").read_text(encoding="utf-8")
    evidence.write_text(
        benchmark.replace("libertyville_il,Libertyville,IL", "libertyville_il,Renamed,IL"),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="place identity changed"):
        execute_run(
            evidence_path=evidence,
            config_dir=Path("config"),
            database_path=database,
            output_dir=tmp_path / "second",
        )


def test_benchmark_brief_rejects_real_evidence(tmp_path: Path) -> None:
    evidence = tmp_path / "real.csv"
    benchmark = Path("data/benchmarks/evidence.csv").read_text(encoding="utf-8")
    evidence.write_text(benchmark.replace(",true,", ",false,"), encoding="utf-8")

    with pytest.raises(ValueError, match="benchmark-only"):
        execute_run(
            evidence_path=evidence,
            config_dir=Path("config"),
            database_path=tmp_path / "run.sqlite",
            output_dir=tmp_path / "output",
        )


def test_real_evidence_report_has_no_synthetic_warning(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    shutil.copytree("config", config_dir)
    brief_path = config_dir / "research_brief.yaml"
    brief = yaml.safe_load(brief_path.read_text(encoding="utf-8"))
    brief["benchmark_only"] = False
    brief_path.write_text(yaml.safe_dump(brief), encoding="utf-8")
    evidence = tmp_path / "real.csv"
    benchmark = Path("data/benchmarks/evidence.csv").read_text(encoding="utf-8")
    evidence.write_text(benchmark.replace(",true,", ",false,"), encoding="utf-8")

    execute_run(
        evidence_path=evidence,
        config_dir=config_dir,
        database_path=tmp_path / "run.sqlite",
        output_dir=tmp_path / "output",
    )

    report = (tmp_path / "output/comparison.md").read_text(encoding="utf-8")
    assert "Synthetic benchmark warning" not in report
    assert "Mixed-evidence warning" not in report


def test_sensitivity_parameters_change_run_identity(tmp_path: Path) -> None:
    first = execute_run(
        evidence_path=Path("data/benchmarks/evidence.csv"),
        config_dir=Path("config"),
        database_path=tmp_path / "runs.sqlite",
        output_dir=tmp_path / "first",
        simulations=1000,
    )
    second = execute_run(
        evidence_path=Path("data/benchmarks/evidence.csv"),
        config_dir=Path("config"),
        database_path=tmp_path / "runs.sqlite",
        output_dir=tmp_path / "second",
        simulations=2000,
    )

    assert first.run_id != second.run_id


def test_profile_budget_changes_purchase_gate_and_eligibility(tmp_path: Path) -> None:
    profile = yaml.safe_load(Path("config/user_profile.example.yaml").read_text(encoding="utf-8"))
    profile["purchase_budget_max"] = 450000
    profile_path = tmp_path / "profile.yaml"
    profile_path.write_text(yaml.safe_dump(profile), encoding="utf-8")
    run = execute_run(
        evidence_path=Path("data/benchmarks/evidence.csv"),
        config_dir=Path("config"),
        profile_path=profile_path,
        database_path=tmp_path / "run.sqlite",
        output_dir=tmp_path / "output",
    )

    purchase_gates = [gate for gate in run.gate_results if gate.gate_id == "purchase_feasibility"]
    assert {gate.threshold for gate in purchase_gates} == {450000}
    assert len(run.scores) == 3


def test_distinct_source_metadata_is_not_collapsed(tmp_path: Path) -> None:
    with Path("data/benchmarks/evidence.csv").open(newline="", encoding="utf-8") as handle:
        source_row = next(csv.DictReader(handle))
    metric_names = [
        name
        for name in source_row
        if name
        not in {
            "place_id",
            "place_name",
            "state",
            "geography_type",
            "source_url",
            "source_title",
            "publisher",
            "tier",
            "retrieved_at",
            "observed_period",
            "observed_at",
            "source_geography",
            "confidence",
            "synthetic",
        }
    ]
    first = dict(source_row)
    second = dict(source_row)
    for metric_name in metric_names:
        first[metric_name] = ""
        second[metric_name] = ""
    first["median_sale_price"] = "510000"
    second["er_drive_minutes"] = "12"
    second["publisher"] = "Independent second publisher"
    evidence = tmp_path / "sources.csv"
    with evidence.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(source_row))
        writer.writeheader()
        writer.writerows((first, second))

    execute_run(
        evidence_path=evidence,
        config_dir=Path("config"),
        database_path=tmp_path / "run.sqlite",
        output_dir=tmp_path / "output",
    )
    session, engine = initialize_database(tmp_path / "run.sqlite")
    try:
        assert {row.publisher for row in session.query(SourceRow).all()} == {
            "Retirement Decision Engine",
            "Independent second publisher",
        }
    finally:
        session.close()
        engine.dispose()


def test_places_outside_configured_regions_are_rejected(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    shutil.copytree("config", config_dir)
    regions_path = config_dir / "regions.yaml"
    regions = yaml.safe_load(regions_path.read_text(encoding="utf-8"))
    regions["regions"][0]["states"] = ["NC"]
    regions_path.write_text(yaml.safe_dump(regions), encoding="utf-8")

    with pytest.raises(ValueError, match="outside configured regions"):
        execute_run(
            evidence_path=Path("data/benchmarks/evidence.csv"),
            config_dir=config_dir,
            database_path=tmp_path / "run.sqlite",
            output_dir=tmp_path / "output",
        )
