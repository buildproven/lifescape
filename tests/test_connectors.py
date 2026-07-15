from pathlib import Path

import pytest

from retirement_engine.config import load_metrics, load_sources
from retirement_engine.evidence import EvidenceError, GeographyMismatchError, ingest_csv


def test_manual_csv_ingests_benchmark() -> None:
    metrics = load_metrics(Path("config"))
    observations = ingest_csv(
        Path("data/benchmarks/evidence.csv"), metrics, load_sources(Path("config"))
    )
    assert len({item.place.place_id for item in observations}) == 10
    assert all(item.source.synthetic for item in observations)


def test_manual_csv_rejects_geography_substitution(tmp_path: Path) -> None:
    csv_path = tmp_path / "bad.csv"
    csv_path.write_text(
        "place_id,place_name,state,geography_type,source_url,source_title,publisher,tier,retrieved_at,observed_period,source_geography,confidence,synthetic,median_sale_price\n"
        "x,Town,NC,town,https://example.gov,Title,Publisher,A,2026-01-01,2025,county,high,true,1\n",
        encoding="utf-8",
    )
    metrics = tuple(item for item in load_metrics(Path("config")) if item.id == "median_sale_price")
    with pytest.raises(GeographyMismatchError):
        ingest_csv(csv_path, metrics, load_sources(Path("config")))


def test_manual_csv_rejects_unknown_columns(tmp_path: Path) -> None:
    csv_path = tmp_path / "bad.csv"
    csv_path.write_text("unexpected\n1\n", encoding="utf-8")
    with pytest.raises(EvidenceError, match="missing columns"):
        ingest_csv(csv_path, (), load_sources(Path("config")))
