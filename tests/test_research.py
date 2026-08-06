from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from lifescape.config import load_metrics
from lifescape.evidence import ingest_csv
from lifescape.models import Confidence, PlaceRecord, SourceRecord, SourceTier
from lifescape.research import (
    ClaudeDiscoveryProvider,
    DiscoveryLead,
    PromotionRequest,
    RejectionRequest,
    ResearchError,
    SearchBrief,
    create_packet,
    export_approved_evidence,
    promote_evidence,
    readiness_for,
    reject_evidence,
    require_complete_packet_evidence,
    state_for,
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


def test_complete_approved_packet_exports_existing_evidence_csv_contract(
    tmp_path: Path, policy
) -> None:
    selected_packet = packet()
    metrics = load_metrics(Path("config"))
    critical = [metric for metric in metrics if metric.critical]
    promotions = tuple(
        promote_evidence(
            promotion(selected_packet.id).model_copy(
                update={
                    "metric_id": metric.id,
                    "raw_value": max(metric.valid_min, min(12, metric.valid_max)),
                }
            ),
            packet=selected_packet,
            metrics=metrics,
            sources=policy,
            as_of=date(2026, 1, 2),
        )
        for metric in critical
    )

    require_complete_packet_evidence(selected_packet, metrics, promotions)
    path = tmp_path / "approved.csv"
    path.write_text(export_approved_evidence(promotions, metrics), encoding="utf-8")
    loaded = ingest_csv(path, metrics, policy, as_of=date(2026, 1, 2))

    assert {(item.place.place_id, item.metric_id) for item in loaded} == {
        ("asheville_nc", metric.id) for metric in critical
    }


def test_rejection_is_a_visible_audit_decision_not_evidence() -> None:
    selected_packet = packet()

    rejected = reject_evidence(
        RejectionRequest(
            packet_id=selected_packet.id,
            reviewer="Brett Stark",
            place=PlaceRecord(place_id="asheville_nc", name="Asheville", state="NC"),
            metric_id="annual_snowfall",
            reason="Station coverage is outside the town boundary.",
        ),
        packet=selected_packet,
        metrics=load_metrics(Path("config")),
    )

    assert rejected.decision.value == "REJECTED"
    assert rejected.reason == "Station coverage is outside the town boundary."


def test_promotion_rejects_discovery_url_and_forged_place_identity(policy) -> None:
    selected_packet = create_packet(
        brief(),
        (
            DiscoveryLead(
                place=PlaceRecord(place_id="asheville_nc", name="Asheville", state="NC"),
                rationale="Discovery lead only.",
                discovery_urls=("https://example.com/discovery",),
            ),
        ),
    )
    request = promotion(selected_packet.id)
    with pytest.raises(ResearchError, match="discovery URLs"):
        promote_evidence(
            request.model_copy(
                update={
                    "source": request.source.model_copy(
                        update={"url": "https://example.com/discovery"}
                    )
                }
            ),
            packet=selected_packet,
            metrics=load_metrics(Path("config")),
            sources=policy,
            as_of=date(2026, 1, 2),
        )
    with pytest.raises(ResearchError, match="exactly match"):
        promote_evidence(
            request.model_copy(
                update={"place": PlaceRecord(place_id="asheville_nc", name="Forged", state="NC")}
            ),
            packet=selected_packet,
            metrics=load_metrics(Path("config")),
            sources=policy,
            as_of=date(2026, 1, 2),
        )


def test_promotion_rejects_blank_reviewer_and_stale_observation(policy) -> None:
    selected_packet = packet()
    with pytest.raises(ValueError, match="human"):
        PromotionRequest.model_validate(
            {**promotion(selected_packet.id).model_dump(mode="json"), "reviewer": "  "}
        )
    with pytest.raises(ResearchError, match="stale"):
        promote_evidence(
            promotion(selected_packet.id).model_copy(update={"observed_at": date(2023, 1, 1)}),
            packet=selected_packet,
            metrics=load_metrics(Path("config")),
            sources=policy,
            as_of=date(2026, 1, 2),
        )


def test_provider_cannot_set_later_lifecycle_state_and_promotions_update_readiness(policy) -> None:
    selected_packet = packet()
    promoted = promote_evidence(
        promotion(selected_packet.id),
        packet=selected_packet,
        metrics=load_metrics(Path("config")),
        sources=policy,
        as_of=date(2026, 1, 2),
    )
    assert (
        "annual_snowfall"
        not in readiness_for(selected_packet, load_metrics(Path("config")), (promoted,))[
            "asheville_nc"
        ]
    )
    assert (
        state_for(selected_packet, load_metrics(Path("config")), (promoted,)).value == "RESEARCHING"
    )


def test_claude_discovery_rejects_a_non_discovery_lifecycle_state() -> None:
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
                                "state": "RANKED",
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
    with (
        patch("lifescape.research.urlopen", return_value=Response()),
        pytest.raises(ResearchError, match="required lead JSON"),
    ):
        provider.discover(brief())


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
