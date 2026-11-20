from __future__ import annotations

from datetime import date

import pytest

from lifescape.models import (
    Confidence,
    ObservationRecord,
    PlaceRecord,
    SourceRecord,
    SourcesConfig,
    SourceTier,
)


@pytest.fixture
def policy() -> SourcesConfig:
    return SourcesConfig(
        allowed_scoring_tiers=frozenset({SourceTier.A, SourceTier.B}),
        discovery_only_tiers=frozenset({SourceTier.C}),
        minimum_gate_confidence=Confidence.HIGH,
        max_age_days=730,
    )


@pytest.fixture
def observation_factory():
    def make(
        place_id: str,
        metric_id: str,
        value: float,
        *,
        tier: SourceTier = SourceTier.A,
        confidence: Confidence = Confidence.HIGH,
    ) -> ObservationRecord:
        return ObservationRecord(
            place=PlaceRecord(place_id=place_id, name=place_id.title(), state="NC"),
            metric_id=metric_id,
            raw_value=value,
            observed_period="2025",
            observed_at=date(2025, 12, 31),
            source=SourceRecord(
                url=f"https://example.gov/{place_id}/{metric_id}",
                title="Evidence",
                publisher="Official source",
                tier=tier,
                retrieved_at=date(2026, 1, 1),
                geography="town",
                confidence=confidence,
                synthetic=True,
            ),
        )

    return make
