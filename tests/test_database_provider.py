from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from lifescape.database_provider import DatabaseEvidenceProvider, provider_from_config
from lifescape.evidence_store import initialize_evidence_store, record_fetch, save_observation
from lifescape.models import (
    Confidence,
    LocalDatabaseConfig,
    ObservationRecord,
    PlaceRecord,
    SourceRecord,
    SourceTier,
)
from lifescape.research import DiscoveryLead, ResearchPacket, SearchBrief, create_packet
from lifescape.research_sources import EvidenceFetchResult


def _place(place_id: str) -> PlaceRecord:
    return PlaceRecord(place_id=place_id, name=place_id.title(), state="NC")


def _observation(place_id: str) -> ObservationRecord:
    return ObservationRecord(
        place=_place(place_id),
        metric_id="median_rent",
        raw_value=1200.0,
        observed_period="2024",
        observed_at=date(2024, 12, 31),
        source=SourceRecord(
            url=f"https://example.org/{place_id}",
            title="Synthetic fixture",
            publisher="Test",
            tier=SourceTier.A,
            retrieved_at=date(2025, 1, 2),
            geography="place",
            confidence=Confidence.HIGH,
            synthetic=True,
        ),
    )


def _packet(*place_ids: str) -> ResearchPacket:
    return create_packet(
        SearchBrief(
            preferences="A walkable, four-season retirement town with outdoor access nearby."
        ),
        tuple(
            DiscoveryLead(place=_place(place_id), rationale="Discovery lead only.")
            for place_id in place_ids
        ),
    )


class RecordingProvider:
    def __init__(self, result: EvidenceFetchResult) -> None:
        self.result = result
        self.asked: list[tuple[str, ...]] = []

    def fetch(self, packet: ResearchPacket) -> EvidenceFetchResult:
        self.asked.append(tuple(lead.place.place_id for lead in packet.leads))
        return self.result


def test_stored_places_are_served_and_only_missing_places_are_fetched(tmp_path: Path) -> None:
    db = tmp_path / "evidence.db"
    initialize_evidence_store(db)
    save_observation(db, _observation("stored_nc"))
    record_fetch(db, "stored_nc", ())
    remote = RecordingProvider(EvidenceFetchResult(observations=(_observation("new_nc"),)))

    result = DatabaseEvidenceProvider(db_path=db, remote_provider=remote).fetch(
        _packet("stored_nc", "new_nc")
    )

    assert remote.asked == [("new_nc",)]
    assert result.observations == (_observation("stored_nc"), _observation("new_nc"))


def test_fetched_observations_are_saved_with_provenance(tmp_path: Path) -> None:
    db = tmp_path / "evidence.db"
    remote = RecordingProvider(EvidenceFetchResult(observations=(_observation("new_nc"),)))
    DatabaseEvidenceProvider(db_path=db, remote_provider=remote).fetch(_packet("new_nc"))

    offline = DatabaseEvidenceProvider(db_path=db).fetch(_packet("new_nc"))

    assert offline.observations == (_observation("new_nc"),)
    assert offline.errors == {}


def test_remote_errors_are_kept(tmp_path: Path) -> None:
    remote = RecordingProvider(EvidenceFetchResult(errors={"new_nc": ("census_acs: down",)}))

    result = DatabaseEvidenceProvider(db_path=tmp_path / "e.db", remote_provider=remote).fetch(
        _packet("new_nc")
    )

    assert result.observations == ()
    assert result.errors == {"new_nc": ("census_acs: down",)}


def test_without_a_remote_a_missing_place_reports_an_error_not_a_value(tmp_path: Path) -> None:
    result = DatabaseEvidenceProvider(db_path=tmp_path / "e.db").fetch(_packet("new_nc"))

    assert result.observations == ()
    assert result.errors == {"new_nc": ("local database: no stored observations",)}


def test_config_puts_the_database_in_front_only_when_enabled(tmp_path: Path) -> None:
    remote = RecordingProvider(EvidenceFetchResult())

    assert provider_from_config(LocalDatabaseConfig(), remote) is remote
    enabled = provider_from_config(
        LocalDatabaseConfig(enabled=True, path=str(tmp_path / "e.db")), remote
    )
    assert isinstance(enabled, DatabaseEvidenceProvider)
    assert enabled.remote_provider is remote


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2025, 3, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


def test_a_partial_fetch_is_fetched_again_and_its_errors_kept(tmp_path: Path) -> None:
    db = tmp_path / "evidence.db"
    remote = RecordingProvider(
        EvidenceFetchResult(
            observations=(_observation("new_nc"),),
            errors={"new_nc": ("noaa_gsoy: explicit source geography is not configured",)},
        )
    )
    provider = DatabaseEvidenceProvider(db_path=db, remote_provider=remote)

    provider.fetch(_packet("new_nc"))
    again = provider.fetch(_packet("new_nc"))

    assert remote.asked == [("new_nc",), ("new_nc",)]
    assert again.errors == {"new_nc": ("noaa_gsoy: explicit source geography is not configured",)}


def test_offline_a_partial_place_serves_what_is_stored_and_says_why(tmp_path: Path) -> None:
    db = tmp_path / "evidence.db"
    remote = RecordingProvider(
        EvidenceFetchResult(
            observations=(_observation("new_nc"),), errors={"new_nc": ("noaa_gsoy: down",)}
        )
    )
    DatabaseEvidenceProvider(db_path=db, remote_provider=remote).fetch(_packet("new_nc"))

    offline = DatabaseEvidenceProvider(db_path=db).fetch(_packet("new_nc"))

    assert offline.observations == (_observation("new_nc"),)
    assert offline.errors == {
        "new_nc": ("local database: last fetch was incomplete: noaa_gsoy: down",)
    }


def test_a_place_older_than_the_limit_is_fetched_again(tmp_path: Path) -> None:
    clock = Clock()
    remote = RecordingProvider(EvidenceFetchResult(observations=(_observation("new_nc"),)))
    provider = DatabaseEvidenceProvider(
        db_path=tmp_path / "e.db", remote_provider=remote, max_age=timedelta(days=30), now=clock
    )
    provider.fetch(_packet("new_nc"))

    clock.now += timedelta(days=29)
    provider.fetch(_packet("new_nc"))
    clock.now += timedelta(days=2)
    provider.fetch(_packet("new_nc"))

    assert remote.asked == [("new_nc",), ("new_nc",)]
