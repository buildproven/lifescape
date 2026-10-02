from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from lifescape.config import load_metrics
from lifescape.models import PlaceRecord
from lifescape.research import (
    ClaudeDiscoveryProvider,
    DiscoveryLead,
    ResearchError,
    SearchBrief,
    create_packet,
    readiness_for,
)


def brief() -> SearchBrief:
    return SearchBrief(
        preferences=(
            "A walkable, four-season retirement town with outdoor access and a lively core."
        ),
        exemplar_towns=("Traverse City, MI",),
        hard_constraints=("Budget below $700,000",),
    )


def _lead(place_id: str) -> DiscoveryLead:
    return DiscoveryLead(
        place=PlaceRecord(place_id=place_id, name=place_id, state="NC"),
        rationale="Discovery lead only.",
    )


def test_search_brief_rejects_invalid_briefs() -> None:
    with pytest.raises(ValueError):
        SearchBrief(preferences="too short")
    with pytest.raises(ValueError):
        SearchBrief(preferences=brief().preferences, exemplar_towns=("A, NC", "B, NC", "C, NC"))


def test_packet_rejects_duplicate_leads() -> None:
    with pytest.raises(ResearchError, match="duplicate"):
        create_packet(brief(), (_lead("asheville_nc"), _lead("asheville_nc")))


def test_packet_rejects_more_than_fifteen_leads() -> None:
    leads = tuple(_lead(f"town_{index}") for index in range(16))
    with pytest.raises(ResearchError, match="at most 15"):
        create_packet(brief(), leads)


def test_packet_records_provider_and_hands_off_unresolved_critical_metrics() -> None:
    result = create_packet(
        brief(), (_lead("a_nc"), _lead("b_nc")), discovery_provider="FixtureProvider"
    )

    assert result.discovery_provider == "FixtureProvider"
    needs = readiness_for(result, load_metrics(Path("config")))
    assert all(len(metrics) == 7 for metrics in needs.values())


def test_claude_discovery_provider_failure_is_a_research_error() -> None:
    provider = ClaudeDiscoveryProvider(api_key="test-key", model="test-model")
    with (
        patch("lifescape.research.urlopen", side_effect=OSError("network down")),
        pytest.raises(ResearchError),
    ):
        provider.discover(brief())
