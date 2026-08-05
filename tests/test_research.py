from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from lifescape.config import load_metrics
from lifescape.models import Confidence, PlaceRecord, SourceRecord, SourceTier
from lifescape.research import (
    ClaudeDiscoveryProvider,
    DiscoveryLead,
    PromotionRequest,
    ResearchError,
    SearchBrief,
    create_packet,
    promote_evidence,
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


def packet():
    return create_packet(
        brief(),
        (
            DiscoveryLead(
                place=PlaceRecord(place_id="asheville_nc", name="Asheville", state="NC"),
                rationale="Discovery lead only.",
            ),
        ),
    )


def promotion(
    packet_id: str, *, tier: SourceTier = SourceTier.A, geography: str = "town"
) -> PromotionRequest:
    return PromotionRequest(
        packet_id=packet_id,
        reviewer="Brett Stark",
        place=PlaceRecord(place_id="asheville_nc", name="Asheville", state="NC"),
        metric_id="annual_snowfall",
        raw_value=12,
        observed_period="2025",
        observed_at=date(2025, 12, 31),
        source=SourceRecord(
            url="https://www.ncei.noaa.gov/example",
            title="Station observation",
            publisher="NOAA",
            tier=tier,
            retrieved_at=date(2026, 1, 1),
            geography=geography,
            confidence=Confidence.HIGH,
        ),
    )


def test_discovery_packet_has_no_ready_critical_evidence() -> None:
    result = readiness_for(packet(), load_metrics(Path("config")))

    assert result == {
        "asheville_nc": (
            "median_sale_price",
            "er_drive_minutes",
            "broadband_mbps_down",
            "annual_snowfall",
            "flood_risk_score",
            "distress_index",
            "one_level_inventory_count",
        )
    }


def test_tier_c_discovery_material_cannot_be_promoted(policy) -> None:
    selected_packet = packet()

    with pytest.raises(ResearchError, match="Tier C"):
        promote_evidence(
            promotion(selected_packet.id, tier=SourceTier.C),
            packet=selected_packet,
            metrics=load_metrics(Path("config")),
            sources=policy,
            as_of=date(2026, 1, 2),
        )


def test_promotion_requires_exact_metric_geography(policy) -> None:
    selected_packet = packet()

    with pytest.raises(ResearchError, match="does not match"):
        promote_evidence(
            promotion(selected_packet.id, geography="county"),
            packet=selected_packet,
            metrics=load_metrics(Path("config")),
            sources=policy,
            as_of=date(2026, 1, 2),
        )


def test_human_reviewed_ready_source_promotes_without_scoring(policy) -> None:
    selected_packet = packet()

    result = promote_evidence(
        promotion(selected_packet.id),
        packet=selected_packet,
        metrics=load_metrics(Path("config")),
        sources=policy,
        as_of=date(2026, 1, 2),
    )

    assert result.packet_id == selected_packet.id
    assert result.reviewer == "Brett Stark"
    assert result.observation.metric_id == "annual_snowfall"


def test_claude_discovery_returns_tier_c_leads_only() -> None:
    payload = {
        "content": [
            {
                "text": json.dumps(
                    {
                        "leads": [
                            {
                                "place": {
                                    "place_id": "asheville_nc",
                                    "name": "Asheville",
                                    "state": "NC",
                                },
                                "rationale": "A possible fit.",
                            }
                        ]
                    }
                )
            }
        ]
    }

    class Response:
        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps(payload).encode()

    provider = ClaudeDiscoveryProvider(api_key="test-key", model="test-model")
    with patch("lifescape.research.urlopen", return_value=Response()) as request:
        leads = provider.discover(brief())

    assert leads[0].state.value == "DISCOVERY"
    assert leads[0].place.place_id == "asheville_nc"
    assert request.call_args.kwargs["timeout"] == 45


def test_claude_discovery_requires_explicit_opt_in_configuration() -> None:
    with pytest.raises(ResearchError, match="ANTHROPIC_API_KEY"):
        ClaudeDiscoveryProvider(api_key="", model="test-model")
    with pytest.raises(ResearchError, match="LIFESCAPE_ANTHROPIC_MODEL"):
        ClaudeDiscoveryProvider(api_key="test-key", model="")
