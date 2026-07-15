import shutil
from pathlib import Path

import pytest
import yaml

from retirement_engine.db import GateResultRow, MetricRow, ResearchRunRow, initialize_database
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
        assert session.query(GateResultRow).filter(GateResultRow.source_id.is_not(None)).count() > 0
    finally:
        session.close()
        engine.dispose()
    assert first.run_id != second.run_id


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
