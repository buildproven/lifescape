"""Deterministic candidate-set normalization."""

from __future__ import annotations

import math

from retirement_engine.models import Direction


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        raise ValueError("cannot calculate percentile of empty values")
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def normalize_values(
    values: dict[str, float],
    direction: Direction,
    *,
    winsor_lower: float = 0.05,
    winsor_upper: float = 0.95,
) -> dict[str, float]:
    """Winsorize and min-max normalize values to 0-10."""
    if not values:
        return {}
    low = percentile(list(values.values()), winsor_lower)
    high = percentile(list(values.values()), winsor_upper)
    if math.isclose(high, low, rel_tol=1e-12, abs_tol=1e-12):
        return {place_id: 5.0 for place_id in values}
    normalized: dict[str, float] = {}
    for place_id, value in values.items():
        clipped = min(max(value, low), high)
        score = (clipped - low) / (high - low) * 10
        if direction is Direction.LOWER:
            score = 10 - score
        normalized[place_id] = round(score, 6)
    return normalized
