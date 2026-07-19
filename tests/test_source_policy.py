from datetime import date

import pytest

from lifescape.evidence import SourcePolicyError, validate_source
from lifescape.models import Confidence, SourceRecord, SourcesConfig, SourceTier


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


def test_future_source_is_rejected(policy) -> None:
    with pytest.raises(SourcePolicyError, match="future"):
        validate_source(
            source(SourceTier.A, retrieved_at=date(2027, 1, 1)),
            policy,
            as_of=date(2026, 1, 2),
        )


def test_synthetic_source_can_be_older_than_policy(policy) -> None:
    old_source = source(SourceTier.A, retrieved_at=date(2010, 1, 1)).model_copy(
        update={"synthetic": True}
    )
    validate_source(old_source, policy, as_of=date(2026, 1, 2))


def test_source_policy_rejects_scoring_discovery_overlap() -> None:
    with pytest.raises(ValueError, match="both scoring and discovery-only"):
        SourcesConfig(
            allowed_scoring_tiers=frozenset({SourceTier.A, SourceTier.C}),
            discovery_only_tiers=frozenset({SourceTier.C}),
            minimum_gate_confidence=Confidence.HIGH,
            max_age_days=730,
        )


def test_discovery_tier_is_rejected_even_if_policy_validation_is_bypassed() -> None:
    invalid_policy = SourcesConfig.model_construct(
        allowed_scoring_tiers=frozenset({SourceTier.C}),
        discovery_only_tiers=frozenset({SourceTier.C}),
        minimum_gate_confidence=Confidence.HIGH,
        max_age_days=730,
    )
    with pytest.raises(SourcePolicyError, match="Tier C"):
        validate_source(source(SourceTier.C), invalid_policy, as_of=date(2026, 1, 2))
