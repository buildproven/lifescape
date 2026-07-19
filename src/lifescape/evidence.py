"""Manual evidence ingestion and source-quality enforcement."""

from __future__ import annotations

import csv
from collections import Counter
from collections.abc import Sequence
from datetime import date
from math import isfinite
from pathlib import Path

from pydantic import ValidationError

from lifescape.models import (
    Confidence,
    MetricDefinition,
    ObservationRecord,
    PlaceRecord,
    SourceRecord,
    SourcesConfig,
    SourceTier,
)


class EvidenceError(ValueError):
    """Base class for evidence contract failures."""


class SourcePolicyError(EvidenceError):
    """Raised when evidence is not eligible to influence a decision."""


class GeographyMismatchError(EvidenceError):
    """Raised when evidence silently substitutes a geography."""


IDENTITY_COLUMNS = {
    "place_id",
    "place_name",
    "state",
    "geography_type",
    "source_url",
    "source_title",
    "publisher",
    "tier",
    "retrieved_at",
    "observed_period",
    "observed_at",
    "source_geography",
    "confidence",
    "synthetic",
}


def validate_unique_headers(fieldnames: Sequence[str]) -> None:
    """Reject ambiguous CSV schemas before DictReader can discard values."""
    duplicates = sorted(name for name, count in Counter(fieldnames).items() if count > 1)
    if duplicates:
        raise EvidenceError(f"evidence CSV has duplicate columns: {duplicates}")


def _parse_boolean(value: str, *, row_number: int, field: str) -> bool:
    normalized = value.strip().lower()
    if normalized not in {"true", "false"}:
        raise EvidenceError(f"row {row_number}: {field} must be true or false")
    return normalized == "true"


def validate_observation_freshness(
    observation_date: date,
    metric: MetricDefinition,
    source: SourceRecord,
    *,
    as_of: date,
) -> None:
    if observation_date > source.retrieved_at:
        raise SourcePolicyError(
            "observation date cannot be after source retrieval date: "
            f"{observation_date.isoformat()} > {source.retrieved_at.isoformat()}"
        )
    if source.synthetic:
        return
    if observation_date > as_of:
        raise SourcePolicyError(
            f"observation date is in the future: {observation_date.isoformat()}"
        )
    if (as_of - observation_date).days > metric.freshness_days:
        raise SourcePolicyError(
            f"observation is stale for {metric.id}: {observation_date.isoformat()}"
        )


def validate_source(
    source: SourceRecord,
    policy: SourcesConfig,
    *,
    for_gate: bool = False,
    as_of: date | None = None,
    max_age_days: int | None = None,
) -> None:
    """Enforce source tier, confidence, geography, and freshness policy."""
    if (
        source.tier in policy.discovery_only_tiers
        or source.tier not in policy.allowed_scoring_tiers
    ):
        raise SourcePolicyError(f"Tier {source.tier} source cannot affect gates or scores")
    confidence_order = {Confidence.LOW: 0, Confidence.MEDIUM: 1, Confidence.HIGH: 2}
    if (
        for_gate
        and confidence_order[source.confidence] < confidence_order[policy.minimum_gate_confidence]
    ):
        raise SourcePolicyError(
            f"{source.confidence} confidence cannot decide a gate; "
            f"minimum is {policy.minimum_gate_confidence}"
        )
    reference_date = as_of or date.today()
    if source.retrieved_at > reference_date:
        raise SourcePolicyError(
            f"source retrieval date is in the future: {source.retrieved_at.isoformat()}"
        )
    allowed_age = (
        policy.max_age_days if max_age_days is None else min(policy.max_age_days, max_age_days)
    )
    if not source.synthetic and (reference_date - source.retrieved_at).days > allowed_age:
        raise SourcePolicyError(f"source is stale: {source.retrieved_at.isoformat()}")


def ingest_csv(
    path: Path,
    metrics: tuple[MetricDefinition, ...],
    policy: SourcesConfig,
    *,
    as_of: date | None = None,
    required_scope: str | None = None,
) -> tuple[ObservationRecord, ...]:
    """Load a wide manual CSV into provenance-preserving observations."""
    metric_map = {metric.id: metric for metric in metrics}
    observations: list[ObservationRecord] = []
    seen_observations: set[tuple[str, str]] = set()
    seen_places: dict[str, PlaceRecord] = {}
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                raise EvidenceError("evidence CSV has no header")
            validate_unique_headers(reader.fieldnames)
            missing = IDENTITY_COLUMNS - set(reader.fieldnames)
            if missing:
                raise EvidenceError(f"evidence CSV missing columns: {sorted(missing)}")
            unknown = set(reader.fieldnames) - IDENTITY_COLUMNS - set(metric_map)
            if unknown:
                raise EvidenceError(f"unknown metric columns: {sorted(unknown)}")
            for row_number, row in enumerate(reader, start=2):
                try:
                    place = PlaceRecord(
                        place_id=row["place_id"],
                        name=row["place_name"],
                        state=row["state"],
                        geography_type=row["geography_type"],
                    )
                    source = SourceRecord(
                        url=row["source_url"],
                        title=row["source_title"],
                        publisher=row["publisher"],
                        tier=SourceTier(row["tier"]),
                        retrieved_at=date.fromisoformat(row["retrieved_at"]),
                        geography=row["source_geography"],
                        confidence=Confidence(row["confidence"]),
                        synthetic=_parse_boolean(
                            row["synthetic"], row_number=row_number, field="synthetic"
                        ),
                    )
                    validate_source(source, policy, as_of=as_of)
                    if source.geography != place.geography_type:
                        raise GeographyMismatchError(
                            f"row {row_number}: source geography {source.geography!r} "
                            f"does not match {place.geography_type!r}"
                        )
                    existing_place = seen_places.get(place.place_id)
                    if existing_place is not None and existing_place != place:
                        raise EvidenceError(f"inconsistent identity for place {place.place_id!r}")
                    seen_places[place.place_id] = place
                    observed_at = date.fromisoformat(row["observed_at"])
                    reference_date = as_of or date.today()
                    row_has_observation = False
                    for metric_id in metric_map:
                        value = row.get(metric_id, "").strip()
                        if value:
                            metric = metric_map[metric_id]
                            if (
                                place.geography_type != metric.geography_level
                                or source.geography != metric.geography_level
                            ):
                                raise GeographyMismatchError(
                                    f"row {row_number}: metric {metric_id!r} requires "
                                    f"{metric.geography_level!r} geography"
                                )
                            if (
                                required_scope is not None
                                and metric.geography_level != required_scope
                            ):
                                raise GeographyMismatchError(
                                    f"row {row_number}: metric {metric_id!r} geography "
                                    f"{metric.geography_level!r} does not match research scope "
                                    f"{required_scope!r}"
                                )
                            key = (place.place_id, metric_id)
                            if key in seen_observations:
                                raise EvidenceError(
                                    f"duplicate observation for place {place.place_id!r} "
                                    f"and metric {metric_id!r}"
                                )
                            validate_observation_freshness(
                                observed_at,
                                metric,
                                source,
                                as_of=reference_date,
                            )
                            raw_value = float(value)
                            if not isfinite(raw_value):
                                raise EvidenceError(
                                    f"row {row_number}: {metric_id} must be a finite number"
                                )
                            if not metric.valid_min <= raw_value <= metric.valid_max:
                                raise EvidenceError(
                                    f"row {row_number}: {metric_id} value {raw_value} is outside "
                                    f"valid range [{metric.valid_min}, {metric.valid_max}]"
                                )
                            observations.append(
                                ObservationRecord(
                                    place=place,
                                    metric_id=metric_id,
                                    raw_value=raw_value,
                                    observed_period=row["observed_period"],
                                    observed_at=observed_at,
                                    source=source,
                                )
                            )
                            seen_observations.add(key)
                            row_has_observation = True
                    if not row_has_observation:
                        raise EvidenceError(f"row {row_number} has no metric values")
                except (KeyError, ValueError, ValidationError) as exc:
                    if isinstance(exc, EvidenceError):
                        raise
                    raise EvidenceError(f"invalid row {row_number}: {exc}") from exc
    except OSError as exc:
        raise EvidenceError(f"cannot read {path}: {exc}") from exc
    return tuple(observations)
