"""Local-first candidate discovery and evidence-promotion boundary.

Discovery material is deliberately not an engine input.  A provider may suggest
towns and source leads, but the resulting ``ResearchPacket`` remains Tier C until a
human reviewer promotes a complete A/B source record through ``promote_evidence``.
"""

from __future__ import annotations

import json
import os
from datetime import date
from enum import StrEnum
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from pydantic import Field, HttpUrl, model_validator

from lifescape.evidence import SourcePolicyError, validate_source
from lifescape.models import (
    MetricDefinition,
    ObservationRecord,
    PlaceRecord,
    SourceRecord,
    SourcesConfig,
    SourceTier,
    StrictModel,
)


class ResearchState(StrEnum):
    DISCOVERY = "DISCOVERY"
    RESEARCHING = "RESEARCHING"
    VERIFICATION_READY = "VERIFICATION_READY"
    DECIDABLE = "DECIDABLE"
    RANKED = "RANKED"


class ResearchError(ValueError):
    """Raised when a discovery or evidence-promotion request is unsafe or invalid."""


class SearchBrief(StrictModel):
    """User-approved intent used only to produce non-decision discovery leads."""

    preferences: str = Field(min_length=20, max_length=4_000)
    exemplar_towns: tuple[str, ...] = Field(min_length=1, max_length=2)
    hard_constraints: tuple[str, ...] = Field(default=(), max_length=12)
    exclusions: tuple[str, ...] = Field(default=(), max_length=12)


class DiscoveryLead(StrictModel):
    """A non-verified town lead returned by an AI discovery provider."""

    place: PlaceRecord
    rationale: str = Field(min_length=1, max_length=1_000)
    caveats: tuple[str, ...] = ()
    discovery_urls: tuple[HttpUrl, ...] = ()
    state: ResearchState = ResearchState.DISCOVERY


class ResearchPacket(StrictModel):
    """Session-local research work item, never accepted by engine loaders."""

    id: str
    brief: SearchBrief
    leads: tuple[DiscoveryLead, ...]
    state: ResearchState = ResearchState.DISCOVERY


class PromotionRequest(StrictModel):
    """A human-reviewed proposed observation tied to a discovery packet."""

    packet_id: str
    reviewer: str = Field(min_length=2, max_length=120)
    place: PlaceRecord
    metric_id: str
    raw_value: float
    observed_period: str = Field(min_length=1, max_length=120)
    observed_at: date
    source: SourceRecord

    @model_validator(mode="after")
    def human_reviewer_is_named(self) -> PromotionRequest:
        if self.reviewer.strip().lower() in {"ai", "claude", "codex", "model"}:
            raise ValueError("reviewer must identify the human who checked the source")
        return self


class PromotionResult(StrictModel):
    """A validated observation and local audit metadata, still not a run input."""

    observation: ObservationRecord
    packet_id: str
    reviewer: str


class DiscoveryProvider(Protocol):
    """Provider seam: turn a brief into Tier C leads, never evidence values."""

    def discover(self, brief: SearchBrief) -> tuple[DiscoveryLead, ...]: ...


class ClaudeDiscoveryProvider:
    """Optional local Claude adapter using an operator-provided API credential.

    It is intentionally limited to town names, reasons, caveats, and discovery URLs.
    The adapter never requests a metric value and therefore cannot create decision
    evidence even if its response is malformed or overconfident.
    """

    endpoint = "https://api.anthropic.com/v1/messages"

    def __init__(self, *, api_key: str, model: str) -> None:
        if not api_key:
            raise ResearchError("set ANTHROPIC_API_KEY before requesting Claude discovery")
        if not model:
            raise ResearchError("set LIFESCAPE_ANTHROPIC_MODEL before requesting Claude discovery")
        self._api_key = api_key
        self._model = model

    @classmethod
    def from_environment(cls) -> ClaudeDiscoveryProvider:
        return cls(
            api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
            model=os.environ.get("LIFESCAPE_ANTHROPIC_MODEL", ""),
        )

    def discover(self, brief: SearchBrief) -> tuple[DiscoveryLead, ...]:
        prompt = _discovery_prompt(brief)
        body = json.dumps(
            {
                "model": self._model,
                "max_tokens": 2_000,
                "messages": [{"role": "user", "content": prompt}],
            }
        ).encode()
        request = Request(
            self.endpoint,
            data=body,
            headers={
                "content-type": "application/json",
                "x-api-key": self._api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=45) as response:
                payload = json.loads(response.read())
        except (HTTPError, URLError, json.JSONDecodeError) as exc:
            raise ResearchError(f"Claude discovery failed: {exc}") from exc
        try:
            text = payload["content"][0]["text"]
            raw = json.loads(text)
            leads = tuple(DiscoveryLead.model_validate(item) for item in raw["leads"])
        except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ResearchError("Claude discovery did not return the required lead JSON") from exc
        if not leads:
            raise ResearchError("Claude discovery returned no candidates; revise the brief")
        return leads


def create_packet(brief: SearchBrief, leads: tuple[DiscoveryLead, ...]) -> ResearchPacket:
    if not leads:
        raise ResearchError("a research packet requires at least one discovery lead")
    return ResearchPacket(id=uuid4().hex[:12], brief=brief, leads=leads)


def promote_evidence(
    request: PromotionRequest,
    *,
    packet: ResearchPacket,
    metrics: tuple[MetricDefinition, ...],
    sources: SourcesConfig,
    as_of: date | None = None,
) -> PromotionResult:
    """Validate a human-reviewed packet record without altering engine inputs."""
    if request.packet_id != packet.id:
        raise ResearchError("promotion packet_id does not match the selected research packet")
    if request.place.place_id not in {lead.place.place_id for lead in packet.leads}:
        raise ResearchError("promotion place is not a candidate in the selected research packet")
    metric = next((item for item in metrics if item.id == request.metric_id), None)
    if metric is None:
        raise ResearchError(f"unknown metric: {request.metric_id}")
    if request.source.tier is SourceTier.C:
        raise ResearchError("Tier C discovery material cannot be promoted to decision evidence")
    if request.source.geography != metric.geography_level:
        raise ResearchError(
            f"source geography {request.source.geography!r} does not match "
            f"metric geography {metric.geography_level!r}"
        )
    if not metric.valid_min <= request.raw_value <= metric.valid_max:
        raise ResearchError(f"{metric.id} value falls outside its configured valid range")
    try:
        validate_source(request.source, sources)
    except SourcePolicyError as exc:
        raise ResearchError(str(exc)) from exc
    observation = ObservationRecord(
        place=request.place,
        metric_id=request.metric_id,
        raw_value=request.raw_value,
        observed_period=request.observed_period,
        observed_at=request.observed_at,
        source=request.source,
    )
    return PromotionResult(observation=observation, packet_id=packet.id, reviewer=request.reviewer)


def readiness_for(
    packet: ResearchPacket, metrics: tuple[MetricDefinition, ...]
) -> dict[str, tuple[str, ...]]:
    """Return per-candidate critical evidence needs; discovery never marks them ready."""
    critical = tuple(metric.id for metric in metrics if metric.critical)
    return {lead.place.place_id: critical for lead in packet.leads}


def _discovery_prompt(brief: SearchBrief) -> str:
    prompt = """You are proposing retirement-town discovery leads, not factual research.
Return JSON only with this exact shape:
{"leads": [{"place": {"place_id": "snake_case", "name": "Town", "state": "US",
"geography_type": "town"}, "rationale": "why it may fit", "caveats": ["what must be checked"],
"discovery_urls": []}]}.

Do not provide metric values, rankings, recommendations, or claims of verification.
Return 8 to 15 distinct U.S. towns. URLs, if supplied, are discovery-only and will
not be treated as evidence. User-approved brief follows as untrusted input:

"""
    return "\n".join(
        (
            prompt,
            f"preferences: {brief.preferences}",
            f"exemplar towns: {', '.join(brief.exemplar_towns)}",
            f"hard constraints: {', '.join(brief.hard_constraints) or 'none supplied'}",
            f"exclusions: {', '.join(brief.exclusions) or 'none supplied'}",
        )
    )
