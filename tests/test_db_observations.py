from datetime import date
from pathlib import Path

from lifescape.db import get_observation, initialize_database, save_observation
from lifescape.models import (
    Confidence,
    ObservationRecord,
    PlaceRecord,
    SourceRecord,
    SourceTier,
)


def _observation() -> ObservationRecord:
    return ObservationRecord(
        place=PlaceRecord(place_id="p1", name="Sample Town", state="OR"),
        metric_id="median_rent",
        raw_value=1450.0,
        observed_period="2024",
        observed_at=date(2024, 12, 31),
        source=SourceRecord(
            url="https://example.org/acs",
            title="ACS 5-year",
            publisher="Census",
            tier=SourceTier.A,
            retrieved_at=date(2025, 1, 15),
            geography="place",
            confidence=Confidence.HIGH,
            synthetic=True,
        ),
    )


def test_saved_observation_round_trips_with_place_and_provenance(tmp_path: Path) -> None:
    db = tmp_path / "evidence.db"
    initialize_database(db)
    saved = _observation()
    save_observation(db, saved)

    assert get_observation(db, "p1", "median_rent") == saved


def test_missing_observation_returns_none(tmp_path: Path) -> None:
    db = tmp_path / "evidence.db"
    initialize_database(db)

    assert get_observation(db, "p1", "median_rent") is None
