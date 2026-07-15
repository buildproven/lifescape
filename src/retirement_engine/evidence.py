"""Manual evidence ingestion and source-quality enforcement."""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from pydantic import ValidationError

from retirement_engine.models import (
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
    "source_geography",
    "confidence",
    "synthetic",
}


def validate_source(
    source: SourceRecord,
    policy: SourcesConfig,
    *,
    for_gate: bool = False,
    as_of: date | None = None,
) -> None:
    """Enforce source tier, confidence, geography, and freshness policy."""
    if source.tier not in policy.allowed_scoring_tiers:
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
    if not source.synthetic and (reference_date - source.retrieved_at).days > policy.max_age_days:
        raise SourcePolicyError(f"source is stale: {source.retrieved_at.isoformat()}")


def ingest_csv(
    path: Path,
    metrics: tuple[MetricDefinition, ...],
    policy: SourcesConfig,
    *,
    as_of: date | None = None,
) -> tuple[ObservationRecord, ...]:
    """Load a wide manual CSV into provenance-preserving observations."""
    metric_map = {metric.id: metric for metric in metrics}
    observations: list[ObservationRecord] = []
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                raise EvidenceError("evidence CSV has no header")
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
                        synthetic=row["synthetic"].strip().lower() == "true",
                    )
                    validate_source(source, policy, as_of=as_of)
                    if source.geography != place.geography_type:
                        raise GeographyMismatchError(
                            f"row {row_number}: source geography {source.geography!r} "
                            f"does not match {place.geography_type!r}"
                        )
                    for metric_id in metric_map:
                        value = row.get(metric_id, "").strip()
                        if value:
                            observations.append(
                                ObservationRecord(
                                    place=place,
                                    metric_id=metric_id,
                                    raw_value=float(value),
                                    observed_period=row["observed_period"],
                                    source=source,
                                )
                            )
                except (KeyError, ValueError, ValidationError) as exc:
                    if isinstance(exc, EvidenceError):
                        raise
                    raise EvidenceError(f"invalid row {row_number}: {exc}") from exc
    except OSError as exc:
        raise EvidenceError(f"cannot read {path}: {exc}") from exc
    return tuple(observations)
