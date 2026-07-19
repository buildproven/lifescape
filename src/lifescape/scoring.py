"""Criterion aggregation, missing-data penalties, and ranking."""

from __future__ import annotations

from collections import defaultdict

from lifescape.models import (
    CriterionScore,
    MetricDefinition,
    ObservationRecord,
    PlaceScore,
    WeightsConfig,
)
from lifescape.normalization import normalize_values


def score_places(
    place_ids: tuple[str, ...],
    observations: tuple[ObservationRecord, ...],
    metrics: tuple[MetricDefinition, ...],
    config: WeightsConfig,
) -> tuple[PlaceScore, ...]:
    """Score only gate-eligible places and return stable tie-broken ranks."""
    eligible = set(place_ids)
    by_metric: dict[str, dict[str, ObservationRecord]] = defaultdict(dict)
    for observation in observations:
        if observation.place.place_id in eligible:
            by_metric[observation.metric_id][observation.place.place_id] = observation

    criterion_metric_scores: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    criterion_sources: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    defined_by_criterion: dict[str, int] = defaultdict(int)
    critical_by_criterion: dict[str, set[str]] = defaultdict(set)
    for metric in metrics:
        if metric.criterion not in config.weights:
            continue
        defined_by_criterion[metric.criterion] += 1
        if metric.critical:
            critical_by_criterion[metric.criterion].add(metric.id)
        records = by_metric.get(metric.id, {})
        normalized = normalize_values(
            {place_id: record.raw_value for place_id, record in records.items()}, metric.direction
        )
        for place_id, value in normalized.items():
            criterion_metric_scores[metric.criterion][place_id].append(value)
            criterion_sources[metric.criterion][place_id].add(records[place_id].source.url)

    unranked: list[tuple[str, float, tuple[CriterionScore, ...]]] = []
    for place_id in sorted(eligible):
        criteria: list[CriterionScore] = []
        total = 0.0
        for criterion, weight in config.weights.items():
            values = criterion_metric_scores[criterion].get(place_id, [])
            expected = defined_by_criterion.get(criterion, 0)
            missing = max(expected - len(values), 0)
            present_metric_ids = {
                metric.id
                for metric in metrics
                if metric.criterion == criterion and place_id in by_metric.get(metric.id, {})
            }
            missing_critical = bool(critical_by_criterion[criterion] - present_metric_ids)
            if values:
                base = sum(values) / len(values)
            else:
                base = 5.0
                missing = max(missing, 1)
            penalty = min(config.missing_noncritical_penalty * missing, 10.0)
            normalized_score = 0.0 if missing_critical else max(base - penalty, 0.0)
            weighted = normalized_score * weight / 100
            criteria.append(
                CriterionScore(
                    place_id=place_id,
                    criterion=criterion,
                    normalized_score=round(normalized_score, 6),
                    weight=weight,
                    weighted_score=round(weighted, 6),
                    missing_penalty=penalty,
                    missing_critical=missing_critical,
                    source_urls=tuple(sorted(criterion_sources[criterion].get(place_id, set()))),
                )
            )
            total += weighted
        unranked.append((place_id, total, tuple(criteria)))
    unranked.sort(key=lambda item: (-item[1], item[0]))
    return tuple(
        PlaceScore(
            place_id=place_id,
            total_score=round(total, 6),
            rank=rank,
            criteria=criteria,
        )
        for rank, (place_id, total, criteria) in enumerate(unranked, start=1)
    )
