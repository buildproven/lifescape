"""Local SQLite evidence store as a research evidence provider.

A place is served from the store only when its last remote fetch was complete
(no errors) and recent. Every other place goes to the remote provider; what it
returns is appended with its provenance, and its errors are recorded and
passed on. The store never invents an observation: a place with no complete,
fresh fetch keeps an error that says why.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from lifescape.evidence_store import (
    initialize_evidence_store,
    last_fetch,
    observations_for_place,
    record_fetch,
    save_observations,
)
from lifescape.models import LocalDatabaseConfig, ObservationRecord
from lifescape.research import DiscoveryLead, ResearchPacket
from lifescape.research_sources import EvidenceFetchResult, ResearchEvidenceProvider


class DatabaseEvidenceProvider:
    """Serves complete, fresh stored places and fetches the rest remotely."""

    def __init__(
        self,
        *,
        db_path: Path | str,
        remote_provider: ResearchEvidenceProvider | None = None,
        max_age: timedelta = timedelta(days=30),
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.db_path = Path(db_path)
        self.remote_provider = remote_provider
        self.max_age = max_age
        self._now = now
        initialize_evidence_store(self.db_path)

    def _stale_reason(self, place_id: str) -> str | None:
        fetch = last_fetch(self.db_path, place_id)
        if fetch is None:
            return "no stored observations"
        fetched_at, errors = fetch
        if errors:
            return "last fetch was incomplete: " + "; ".join(errors)
        if self._now() - fetched_at > self.max_age:
            return f"stored observations are older than {self.max_age.days} days"
        return None

    def fetch(self, packet: ResearchPacket) -> EvidenceFetchResult:
        observations: list[ObservationRecord] = []
        errors: dict[str, tuple[str, ...]] = {}
        refetch: list[DiscoveryLead] = []
        reasons: dict[str, str] = {}
        for lead in packet.leads:
            place_id = lead.place.place_id
            reason = self._stale_reason(place_id)
            if reason is None:
                observations.extend(observations_for_place(self.db_path, place_id))
            else:
                refetch.append(lead)
                reasons[place_id] = reason

        if not refetch:
            return EvidenceFetchResult(observations=tuple(observations), errors=errors)

        if self.remote_provider is None:
            # Offline: serve what is stored, and say why it may be incomplete or old.
            for lead in refetch:
                place_id = lead.place.place_id
                observations.extend(observations_for_place(self.db_path, place_id))
                errors[place_id] = (f"local database: {reasons[place_id]}",)
            return EvidenceFetchResult(observations=tuple(observations), errors=errors)

        remote = self.remote_provider.fetch(packet.model_copy(update={"leads": tuple(refetch)}))
        save_observations(self.db_path, list(remote.observations))
        fetched_at = self._now()
        for lead in refetch:
            place_id = lead.place.place_id
            record_fetch(self.db_path, place_id, remote.errors.get(place_id, ()), fetched_at)
        observations.extend(remote.observations)
        errors.update(remote.errors)
        return EvidenceFetchResult(observations=tuple(observations), errors=errors)


def provider_from_config(
    config: LocalDatabaseConfig, remote_provider: ResearchEvidenceProvider
) -> ResearchEvidenceProvider:
    """The local database in front of the remote provider when enabled, else the remote one."""
    if not config.enabled:
        return remote_provider
    return DatabaseEvidenceProvider(
        db_path=config.path,
        remote_provider=remote_provider,
        max_age=timedelta(days=config.max_age_days),
    )
