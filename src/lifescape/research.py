"""Local-first candidate discovery and evidence-promotion boundary.

Discovery material is deliberately not an engine input.  A provider may suggest
towns and source leads, but the resulting ``ResearchPacket`` remains Tier C until a
human reviewer promotes a complete A/B source record through ``promote_evidence``.
"""

from __future__ import annotations

import csv
import io
import json
import os
from datetime import date
from enum import StrEnum
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen
from uuid import uuid4

from pydantic import Field, HttpUrl, model_validator

from lifescape.evidence import (
    IDENTITY_COLUMNS,
    SourcePolicyError,
    validate_observation_freshness,
    validate_source,
)
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


class ReviewDecision(StrEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ResearchError(ValueError):
    """Raised when a discovery or evidence-promotion request is unsafe or invalid."""


MAX_DISCOVERY_RESPONSE_BYTES = 1_000_000
MAX_DISCOVERY_LEADS = 15


class SearchBrief(StrictModel):
    """User-approved intent used only to produce non-decision discovery leads."""

    preferences: str = Field(min_length=20, max_length=4_000)
    exemplar_towns: tuple[str, ...] = Field(default=(), min_length=0, max_length=2)
    hard_constraints: tuple[str, ...] = Field(default=(), max_length=12)
    exclusions: tuple[str, ...] = Field(default=(), max_length=12)


class DiscoveryLead(StrictModel):
    """A non-verified town lead returned by an AI discovery provider."""

    place: PlaceRecord
    rationale: str = Field(min_length=1, max_length=1_000)
    caveats: tuple[str, ...] = ()
    discovery_urls: tuple[HttpUrl, ...] = ()
    connector_geographies: dict[str, str] = Field(default_factory=dict)
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
        normalized = self.reviewer.strip().lower()
        if not normalized:
            raise ValueError("reviewer must identify the human who checked the source")
        if normalized in {"ai", "claude", "codex", "model"}:
            raise ValueError("reviewer must identify the human who checked the source")
        return self


class FetchedPromotionRequest(StrictModel):
    """Approve one adapter-fetched observation without re-entering its value."""

    packet_id: str
    place_id: str
    metric_id: str
    reviewer: str = Field(min_length=2, max_length=120)

    @model_validator(mode="after")
    def human_reviewer_is_named(self) -> FetchedPromotionRequest:
        normalized = self.reviewer.strip().lower()
        if not normalized or normalized in {"ai", "claude", "codex", "model"}:
            raise ValueError("reviewer must identify the human who checked the source")
        return self


class ResearchSelectionRequest(StrictModel):
    """Select a bounded subset of discovery leads for evidence retrieval."""

    packet_id: str
    place_ids: tuple[str, ...] = Field(min_length=2, max_length=15)


class PromotionResult(StrictModel):
    """A validated observation and local audit metadata, still not a run input."""

    observation: ObservationRecord
    packet_id: str
    reviewer: str


class RejectionRequest(StrictModel):
    """A named reviewer rejects a proposed source record without creating evidence."""

    packet_id: str
    reviewer: str = Field(min_length=2, max_length=120)
    place: PlaceRecord
    metric_id: str
    reason: str = Field(min_length=8, max_length=1_000)

    @model_validator(mode="after")
    def human_reviewer_is_named(self) -> RejectionRequest:
        normalized = self.reviewer.strip().lower()
        if not normalized or normalized in {"ai", "claude", "codex", "model"}:
            raise ValueError("reviewer must identify the human who checked the source")
        return self


class ReviewRecord(StrictModel):
    """Visible local audit record for an approve or reject decision."""

    packet_id: str
    place_id: str
    metric_id: str
    reviewer: str
    decision: ReviewDecision
    reason: str | None = None


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
                headers = getattr(response, "headers", {})
                content_length = headers.get("Content-Length")
                try:
                    response_size = int(content_length) if content_length is not None else 0
                except ValueError as exc:
                    raise ResearchError(
                        "Claude discovery returned an invalid Content-Length"
                    ) from exc
                if response_size > MAX_DISCOVERY_RESPONSE_BYTES:
                    raise ResearchError("Claude discovery response exceeds the 1 MB safety limit")
                payload_bytes = response.read(MAX_DISCOVERY_RESPONSE_BYTES + 1)
                if len(payload_bytes) > MAX_DISCOVERY_RESPONSE_BYTES:
                    raise ResearchError("Claude discovery response exceeds the 1 MB safety limit")
                payload = json.loads(payload_bytes)
        except (HTTPError, URLError, json.JSONDecodeError) as exc:
            raise ResearchError(f"Claude discovery failed: {exc}") from exc
        try:
            text = payload["content"][0]["text"]
            raw = json.loads(text)
            raw_leads = raw["leads"]
            if not isinstance(raw_leads, list) or len(raw_leads) > MAX_DISCOVERY_LEADS:
                raise ValueError("discovery response has an invalid number of leads")
            leads = tuple(_validate_discovery_lead(item) for item in raw_leads)
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
    lead = next(
        (item for item in packet.leads if item.place.place_id == request.place.place_id), None
    )
    if lead is None:
        raise ResearchError("promotion place is not a candidate in the selected research packet")
    if request.place != lead.place:
        raise ResearchError("promotion place must exactly match the selected research-packet lead")
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
    if request.place.geography_type != metric.geography_level:
        raise ResearchError(
            f"place geography {request.place.geography_type!r} does not match "
            f"metric geography {metric.geography_level!r}"
        )
    discovery_urls = {
        _canonical_source_url(str(url)) for item in packet.leads for url in item.discovery_urls
    }
    if _canonical_source_url(request.source.url) in discovery_urls:
        raise ResearchError("discovery URLs cannot be promoted to decision evidence")
    if not metric.valid_min <= request.raw_value <= metric.valid_max:
        raise ResearchError(f"{metric.id} value falls outside its configured valid range")
    try:
        reference_date = as_of or date.today()
        validate_source(request.source, sources, as_of=reference_date)
        validate_observation_freshness(
            request.observed_at,
            metric,
            request.source,
            as_of=reference_date,
        )
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
    return PromotionResult(
        observation=observation,
        packet_id=packet.id,
        reviewer=request.reviewer.strip(),
    )


def reject_evidence(
    request: RejectionRequest,
    *,
    packet: ResearchPacket,
    metrics: tuple[MetricDefinition, ...],
) -> ReviewRecord:
    """Record a rejection only after binding it to a real packet lead and metric."""
    if request.packet_id != packet.id:
        raise ResearchError("rejection packet_id does not match the selected research packet")
    lead = next(
        (item for item in packet.leads if item.place.place_id == request.place.place_id), None
    )
    if lead is None or request.place != lead.place:
        raise ResearchError("rejection place must exactly match a selected research-packet lead")
    if request.metric_id not in {metric.id for metric in metrics}:
        raise ResearchError(f"unknown metric: {request.metric_id}")
    return ReviewRecord(
        packet_id=packet.id,
        place_id=request.place.place_id,
        metric_id=request.metric_id,
        reviewer=request.reviewer.strip(),
        decision=ReviewDecision.REJECTED,
        reason=request.reason.strip(),
    )


def promote_fetched_evidence(
    request: FetchedPromotionRequest,
    *,
    packet: ResearchPacket,
    observation: ObservationRecord,
    metrics: tuple[MetricDefinition, ...],
    sources: SourcesConfig,
    as_of: date | None = None,
) -> PromotionResult:
    """Promote a server-fetched observation after a named human review."""
    if request.packet_id != packet.id:
        raise ResearchError("promotion packet_id does not match the selected research packet")
    lead = next((item for item in packet.leads if item.place.place_id == request.place_id), None)
    if lead is None:
        raise ResearchError("promotion place is not a candidate in the selected research packet")
    if observation.place != lead.place:
        raise ResearchError("fetched observation place does not match the selected packet lead")
    if observation.metric_id != request.metric_id:
        raise ResearchError("fetched observation metric does not match the requested metric")
    metric = next((item for item in metrics if item.id == observation.metric_id), None)
    if metric is None:
        raise ResearchError(f"unknown metric: {observation.metric_id}")
    if observation.place.geography_type != metric.geography_level:
        raise ResearchError(
            f"fetched observation geography {observation.place.geography_type!r} does not match "
            f"metric {metric.id!r} geography {metric.geography_level!r}"
        )
    if observation.source.geography != metric.geography_level:
        raise ResearchError(
            f"fetched source geography {observation.source.geography!r} does not match "
            f"metric {metric.id!r} geography {metric.geography_level!r}"
        )
    if not metric.valid_min <= observation.raw_value <= metric.valid_max:
        raise ResearchError(f"{metric.id} value falls outside its configured valid range")
    try:
        reference_date = as_of or date.today()
        validate_source(
            observation.source,
            sources,
            for_gate=metric.critical,
            as_of=reference_date,
        )
        validate_observation_freshness(
            observation.observed_at,
            metric,
            observation.source,
            as_of=reference_date,
        )
    except SourcePolicyError as exc:
        raise ResearchError(str(exc)) from exc
    return PromotionResult(
        observation=observation,
        packet_id=packet.id,
        reviewer=request.reviewer.strip(),
    )


def select_packet_leads(
    request: ResearchSelectionRequest, *, packet: ResearchPacket
) -> ResearchPacket:
    """Create a new packet containing exactly the selected discovery leads."""
    if request.packet_id != packet.id:
        raise ResearchError("selection packet_id does not match the selected research packet")
    selected_ids = set(request.place_ids)
    if len(selected_ids) != len(request.place_ids):
        raise ResearchError("selected discovery leads must be unique")
    leads = tuple(lead for lead in packet.leads if lead.place.place_id in selected_ids)
    if len(leads) != len(selected_ids):
        raise ResearchError("selection contains a place that is not a discovery lead")
    return ResearchPacket(
        id=uuid4().hex[:12],
        brief=packet.brief,
        leads=leads,
    )


def export_approved_evidence(
    promotions: tuple[PromotionResult, ...], metrics: tuple[MetricDefinition, ...]
) -> str:
    """Write approved observations as the existing wide evidence CSV contract.

    Each row carries exactly one metric and its own source block.  This preserves
    metric-level provenance even when two facts about a town come from different
    primary sources; ``ingest_csv`` already accepts that normalized wide form.
    """
    metric_ids = tuple(metric.id for metric in metrics)
    fieldnames = sorted(IDENTITY_COLUMNS) + list(metric_ids)
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    seen: set[tuple[str, str]] = set()
    for item in sorted(
        promotions,
        key=lambda value: (value.observation.place.place_id, value.observation.metric_id),
    ):
        observation = item.observation
        key = (observation.place.place_id, observation.metric_id)
        if key in seen:
            raise ResearchError(
                "multiple approved observations for "
                f"{observation.place.place_id!r} and {observation.metric_id!r}"
            )
        seen.add(key)
        source = observation.source
        row: dict[str, str | float] = {field: "" for field in fieldnames}
        row.update(
            {
                "place_id": observation.place.place_id,
                "place_name": observation.place.name,
                "state": observation.place.state,
                "geography_type": observation.place.geography_type,
                "source_url": source.url,
                "source_title": source.title,
                "publisher": source.publisher,
                "tier": source.tier.value,
                "retrieved_at": source.retrieved_at.isoformat(),
                "observed_period": observation.observed_period,
                "observed_at": observation.observed_at.isoformat(),
                "source_geography": source.geography,
                "confidence": source.confidence.value,
                "synthetic": str(source.synthetic).lower(),
                observation.metric_id: observation.raw_value,
            }
        )
        writer.writerow(row)
    return stream.getvalue()


def require_complete_packet_evidence(
    packet: ResearchPacket,
    metrics: tuple[MetricDefinition, ...],
    promotions: tuple[PromotionResult, ...],
) -> None:
    """Fail closed until every packet candidate has approved critical evidence."""
    missing = readiness_for(packet, metrics, promotions)
    unresolved = {place_id: values for place_id, values in missing.items() if values}
    if unresolved:
        raise ResearchError(f"critical evidence remains unapproved: {unresolved}")


def readiness_for(
    packet: ResearchPacket,
    metrics: tuple[MetricDefinition, ...],
    promotions: tuple[PromotionResult, ...] = (),
) -> dict[str, tuple[str, ...]]:
    """Return per-candidate critical evidence needs; discovery never marks them ready."""
    critical = tuple(metric.id for metric in metrics if metric.critical)
    promoted = {
        (item.observation.place.place_id, item.observation.metric_id) for item in promotions
    }
    return {
        lead.place.place_id: tuple(
            metric_id for metric_id in critical if (lead.place.place_id, metric_id) not in promoted
        )
        for lead in packet.leads
    }


def state_for(
    packet: ResearchPacket,
    metrics: tuple[MetricDefinition, ...],
    promotions: tuple[PromotionResult, ...] = (),
) -> ResearchState:
    """Calculate packet state from accepted local promotion audit records."""
    if not promotions:
        return ResearchState.DISCOVERY
    if all(not needs for needs in readiness_for(packet, metrics, promotions).values()):
        return ResearchState.DECIDABLE
    return ResearchState.RESEARCHING


def _validate_discovery_lead(value: object) -> DiscoveryLead:
    if not isinstance(value, dict):
        raise ValueError("each discovery lead must be an object")
    supplied_state = value.get("state", ResearchState.DISCOVERY)
    if supplied_state != ResearchState.DISCOVERY:
        raise ValueError("discovery providers may only return DISCOVERY-state leads")
    return DiscoveryLead.model_validate({**value, "state": ResearchState.DISCOVERY})


def _canonical_source_url(value: str) -> str:
    """Compare discovery and proposed source URLs without query/slash evasions."""
    parsed = urlsplit(value)
    hostname = parsed.hostname.lower() if parsed.hostname else ""
    if not hostname:
        return value
    port = parsed.port
    if (parsed.scheme == "https" and port == 443) or (parsed.scheme == "http" and port == 80):
        port = None
    netloc = hostname if port is None else f"{hostname}:{port}"
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme.lower(), netloc, path, "", ""))


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
