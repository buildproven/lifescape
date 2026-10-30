"""Local SQLite evidence store as a research evidence provider.

Observations already stored for a place are served from the store; only the
places with none go to the remote provider, and what it returns is saved with
its source provenance. The store never invents an observation: a place with no
stored or fetched observation keeps its error.
"""

from __future__ import annotations

from pathlib import Path

from lifescape.evidence_store import (
    initialize_evidence_store,
    observations_for_place,
    save_observations,
)
from lifescape.models import LocalDatabaseConfig, ObservationRecord
from lifescape.research import ResearchPacket
from lifescape.research_sources import EvidenceFetchResult, ResearchEvidenceProvider


class DatabaseEvidenceProvider:
    """Serves stored observations and fetches the rest from a remote provider."""

    def __init__(
        self, *, db_path: Path | str, remote_provider: ResearchEvidenceProvider | None = None
    ) -> None:
        self.db_path = Path(db_path)
        self.remote_provider = remote_provider
        initialize_evidence_store(self.db_path)

    def fetch(self, packet: ResearchPacket) -> EvidenceFetchResult:
        observations: list[ObservationRecord] = []
        errors: dict[str, tuple[str, ...]] = {}
        missing = []
        for lead in packet.leads:
            stored = observations_for_place(self.db_path, lead.place.place_id)
            if stored:
                observations.extend(stored)
            else:
                missing.append(lead)

        if missing and self.remote_provider is not None:
            remote = self.remote_provider.fetch(packet.model_copy(update={"leads": tuple(missing)}))
            save_observations(self.db_path, list(remote.observations))
            observations.extend(remote.observations)
            errors.update(remote.errors)
        else:
            for lead in missing:
                errors[lead.place.place_id] = ("local database: no stored observations",)

        return EvidenceFetchResult(observations=tuple(observations), errors=errors)


def provider_from_config(
    config: LocalDatabaseConfig, remote_provider: ResearchEvidenceProvider
) -> ResearchEvidenceProvider:
    """The local database in front of the remote provider when enabled, else the remote one."""
    if not config.enabled:
        return remote_provider
    return DatabaseEvidenceProvider(db_path=config.path, remote_provider=remote_provider)
