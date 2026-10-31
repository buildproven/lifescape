import sqlite3
from datetime import date
from pathlib import Path

import pytest

from lifescape.evidence_store import (
    EvidenceStoreError,
    get_observation,
    initialize_evidence_store,
    observation_history,
    observations_for_place,
    save_observation,
    save_observations,
)
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
    initialize_evidence_store(db)
    saved = _observation()
    save_observation(db, saved)

    assert get_observation(db, "p1", "median_rent") == saved


def test_missing_observation_returns_none(tmp_path: Path) -> None:
    db = tmp_path / "evidence.db"
    initialize_evidence_store(db)

    assert get_observation(db, "p1", "median_rent") is None


def _variant(**source: object) -> ObservationRecord:
    base = _observation()
    return base.model_copy(update={"source": base.source.model_copy(update=source)})


def test_sources_sharing_a_url_keep_their_own_provenance(tmp_path: Path) -> None:
    db = tmp_path / "evidence.db"
    initialize_evidence_store(db)
    official = _observation()
    derived = _variant(title="Derived index (not an official Census statistic)").model_copy(
        update={"metric_id": "distress_index"}
    )
    save_observations(db, [official, derived])

    assert get_observation(db, "p1", "median_rent") == official
    assert get_observation(db, "p1", "distress_index") == derived


def test_a_refetch_is_appended_and_read_back_with_its_own_dates(tmp_path: Path) -> None:
    db = tmp_path / "evidence.db"
    initialize_evidence_store(db)
    first = _observation()
    newer = _variant(retrieved_at=date(2025, 6, 1)).model_copy(update={"raw_value": 1500.0})
    save_observation(db, first)
    save_observation(db, newer)
    save_observation(db, newer)

    assert get_observation(db, "p1", "median_rent") == newer
    assert observation_history(db, "p1", "median_rent") == (first, newer)


def test_a_corrected_place_record_is_read_back(tmp_path: Path) -> None:
    db = tmp_path / "evidence.db"
    initialize_evidence_store(db)
    save_observation(db, _observation())
    corrected = _observation().model_copy(
        update={"place": PlaceRecord(place_id="p1", name="Sample City", state="OR")}
    )
    save_observation(db, corrected)

    assert observations_for_place(db, "p1") == (corrected,)


def test_a_file_that_is_not_an_evidence_store_is_refused(tmp_path: Path) -> None:
    db = tmp_path / "other.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE observations (place_id TEXT)")

    with pytest.raises(EvidenceStoreError):
        initialize_evidence_store(db)
