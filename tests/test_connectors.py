from datetime import date
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
        "place_id,place_name,state,geography_type,source_url,source_title,publisher,tier,retrieved_at,observed_period,observed_at,source_geography,confidence,synthetic,median_sale_price\n"
        "x,Town,NC,town,https://example.gov,Title,Publisher,A,2026-01-01,2025,2025-12-31,county,high,true,1\n",
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


def test_manual_csv_rejects_duplicate_observations(tmp_path: Path) -> None:
    header = (
        "place_id,place_name,state,geography_type,source_url,source_title,publisher,tier,"
        "retrieved_at,observed_period,observed_at,source_geography,confidence,synthetic,median_sale_price\n"
    )
    row = "x,Town,NC,town,https://example.gov,Title,Publisher,A,2026-01-01,2025,2025-12-31,town,high,true,1\n"
    csv_path = tmp_path / "duplicate.csv"
    csv_path.write_text(header + row + row, encoding="utf-8")
    metrics = tuple(item for item in load_metrics(Path("config")) if item.id == "median_sale_price")
    with pytest.raises(EvidenceError, match="duplicate observation"):
        ingest_csv(csv_path, metrics, load_sources(Path("config")))


def test_manual_csv_enforces_metric_freshness(tmp_path: Path) -> None:
    csv_path = tmp_path / "stale.csv"
    csv_path.write_text(
        "place_id,place_name,state,geography_type,source_url,source_title,publisher,tier,"
        "retrieved_at,observed_period,observed_at,source_geography,confidence,synthetic,median_sale_price\n"
        "x,Town,NC,town,https://example.gov,Title,Publisher,A,2026-01-01,2025,2026-01-01,town,high,false,1\n",
        encoding="utf-8",
    )
    metric = next(item for item in load_metrics(Path("config")) if item.id == "median_sale_price")
    strict_metric = metric.model_copy(update={"freshness_days": 30})
    with pytest.raises(EvidenceError, match="stale"):
        ingest_csv(
            csv_path,
            (strict_metric,),
            load_sources(Path("config")),
            as_of=date(2026, 2, 1),
        )


@pytest.mark.parametrize("synthetic", ["true", "false"])
def test_manual_csv_rejects_observation_after_source_retrieval(
    tmp_path: Path, synthetic: str
) -> None:
    csv_path = tmp_path / f"impossible-chronology-{synthetic}.csv"
    csv_path.write_text(
        "place_id,place_name,state,geography_type,source_url,source_title,publisher,tier,"
        "retrieved_at,observed_period,observed_at,source_geography,confidence,synthetic,median_sale_price\n"
        f"x,Town,NC,town,https://example.gov,Title,Publisher,A,2026-01-01,2026,2026-01-02,town,high,{synthetic},100000\n",
        encoding="utf-8",
    )
    metric = next(item for item in load_metrics(Path("config")) if item.id == "median_sale_price")
    with pytest.raises(EvidenceError, match="after source retrieval"):
        ingest_csv(
            csv_path,
            (metric,),
            load_sources(Path("config")),
            as_of=date(2026, 1, 3),
        )


def test_manual_csv_rejects_nonfinite_values(tmp_path: Path) -> None:
    csv_path = tmp_path / "nonfinite.csv"
    csv_path.write_text(
        "place_id,place_name,state,geography_type,source_url,source_title,publisher,tier,"
        "retrieved_at,observed_period,observed_at,source_geography,confidence,synthetic,median_sale_price\n"
        "x,Town,NC,town,https://example.gov,Title,Publisher,A,2026-01-01,2025,2025-12-31,town,high,true,nan\n",
        encoding="utf-8",
    )
    metric = next(item for item in load_metrics(Path("config")) if item.id == "median_sale_price")
    with pytest.raises(EvidenceError, match="finite number"):
        ingest_csv(csv_path, (metric,), load_sources(Path("config")))


def test_manual_csv_rejects_values_outside_metric_range(tmp_path: Path) -> None:
    csv_path = tmp_path / "impossible.csv"
    csv_path.write_text(
        "place_id,place_name,state,geography_type,source_url,source_title,publisher,tier,"
        "retrieved_at,observed_period,observed_at,source_geography,confidence,synthetic,er_drive_minutes\n"
        "x,Town,NC,town,https://example.gov,Title,Publisher,A,2026-01-01,2025,2025-12-31,town,high,true,-30\n",
        encoding="utf-8",
    )
    metric = next(item for item in load_metrics(Path("config")) if item.id == "er_drive_minutes")
    with pytest.raises(EvidenceError, match="outside valid range"):
        ingest_csv(csv_path, (metric,), load_sources(Path("config")))


def test_manual_csv_enforces_metric_geography_even_when_row_matches(tmp_path: Path) -> None:
    csv_path = tmp_path / "wrong-level.csv"
    csv_path.write_text(
        "place_id,place_name,state,geography_type,source_url,source_title,publisher,tier,"
        "retrieved_at,observed_period,observed_at,source_geography,confidence,synthetic,median_sale_price\n"
        "x,Town,NC,county,https://example.gov,Title,Publisher,A,2026-01-01,2025,2025-12-31,county,high,true,100000\n",
        encoding="utf-8",
    )
    metric = next(item for item in load_metrics(Path("config")) if item.id == "median_sale_price")
    with pytest.raises(GeographyMismatchError, match="requires 'town'"):
        ingest_csv(csv_path, (metric,), load_sources(Path("config")), required_scope="town")


def test_manual_csv_rejects_rows_without_metric_values(tmp_path: Path) -> None:
    csv_path = tmp_path / "empty-row.csv"
    csv_path.write_text(
        "place_id,place_name,state,geography_type,source_url,source_title,publisher,tier,"
        "retrieved_at,observed_period,observed_at,source_geography,confidence,synthetic,median_sale_price\n"
        "x,Town,NC,town,https://example.gov,Title,Publisher,A,2026-01-01,2025,2025-12-31,town,high,true,\n",
        encoding="utf-8",
    )
    metric = next(item for item in load_metrics(Path("config")) if item.id == "median_sale_price")
    with pytest.raises(EvidenceError, match="no metric values"):
        ingest_csv(csv_path, (metric,), load_sources(Path("config")))


def test_manual_csv_rejects_ambiguous_synthetic_flag(tmp_path: Path) -> None:
    csv_path = tmp_path / "ambiguous.csv"
    csv_path.write_text(
        "place_id,place_name,state,geography_type,source_url,source_title,publisher,tier,"
        "retrieved_at,observed_period,observed_at,source_geography,confidence,synthetic,median_sale_price\n"
        "x,Town,NC,town,https://example.gov,Title,Publisher,A,2026-01-01,2025,2025-12-31,town,high,tru,1\n",
        encoding="utf-8",
    )
    metric = next(item for item in load_metrics(Path("config")) if item.id == "median_sale_price")
    with pytest.raises(EvidenceError, match="must be true or false"):
        ingest_csv(csv_path, (metric,), load_sources(Path("config")))
