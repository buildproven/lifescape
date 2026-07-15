from pathlib import Path

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
