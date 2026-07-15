"""Seeded Monte Carlo weight sensitivity analysis."""

from __future__ import annotations

import random
from collections import defaultdict
from statistics import fmean, pvariance

from retirement_engine.models import PlaceScore, SensitivityResult


def analyze_sensitivity(
    scores: tuple[PlaceScore, ...],
    *,
    simulations: int = 1000,
    seed: int = 20260714,
) -> tuple[SensitivityResult, ...]:
    if simulations < 1000:
        raise ValueError("at least 1,000 simulations are required")
    if not scores:
        return ()
    rng = random.Random(seed)
    criteria = {item.criterion: item.weight for item in scores[0].criteria}
    value_map = {
        place.place_id: {item.criterion: item.normalized_score for item in place.criteria}
        for place in scores
    }
    ranks: dict[str, list[int]] = defaultdict(list)
    top_three: dict[str, int] = defaultdict(int)
    for _ in range(simulations):
        perturbed = {
            criterion: weight * rng.uniform(0.75, 1.25) for criterion, weight in criteria.items()
        }
        total_weight = sum(perturbed.values())
        normalized_weights = {
            criterion: weight / total_weight * 100 for criterion, weight in perturbed.items()
        }
        ordered = sorted(
            value_map,
            key=lambda place_id: (
                -sum(
                    value_map[place_id][criterion] * weight / 100
                    for criterion, weight in normalized_weights.items()
                ),
                place_id,
            ),
        )
        for rank, place_id in enumerate(ordered, start=1):
            ranks[place_id].append(rank)
            if rank <= min(3, len(ordered)):
                top_three[place_id] += 1
    return tuple(
        SensitivityResult(
            place_id=place.place_id,
            top_three_frequency=round(top_three[place.place_id] / simulations, 6),
            mean_rank=round(fmean(ranks[place.place_id]), 6),
            rank_variance=round(pvariance(ranks[place.place_id]), 6),
            fragile=top_three[place.place_id] / simulations < 0.6,
        )
        for place in scores
    )
