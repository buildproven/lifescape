from datetime import date

import pytest

from retirement_engine.evidence import SourcePolicyError, validate_source
from retirement_engine.models import Confidence, SourceRecord, SourceTier


def source(tier: SourceTier, *, retrieved_at: date = date(2026, 1, 1)) -> SourceRecord:
    return SourceRecord(
        url="https://example.com",
        title="Source",
        publisher="Publisher",
        tier=tier,
        retrieved_at=retrieved_at,
        geography="town",
        confidence=Confidence.HIGH,
    )


def test_tier_c_is_never_decision_evidence(policy) -> None:
    with pytest.raises(SourcePolicyError, match="Tier C"):
        validate_source(source(SourceTier.C), policy, as_of=date(2026, 1, 2))


def test_tier_a_is_allowed(policy) -> None:
    validate_source(source(SourceTier.A), policy, as_of=date(2026, 1, 2))


def test_stale_non_synthetic_source_is_rejected(policy) -> None:
    with pytest.raises(SourcePolicyError, match="stale"):
        validate_source(
            source(SourceTier.A, retrieved_at=date(2020, 1, 1)), policy, as_of=date(2026, 1, 2)
        )
