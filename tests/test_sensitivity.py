import pytest

from retirement_engine.models import CriterionScore, PlaceScore
from retirement_engine.sensitivity import analyze_sensitivity


def place(place_id: str, rank: int, first: float, second: float) -> PlaceScore:
    criteria = (
        CriterionScore(
            place_id=place_id,
            criterion="a",
            normalized_score=first,
            weight=50,
            weighted_score=first / 2,
        ),
        CriterionScore(
            place_id=place_id,
            criterion="b",
            normalized_score=second,
            weight=50,
            weighted_score=second / 2,
        ),
    )
    return PlaceScore(
        place_id=place_id, total_score=(first + second) / 2, rank=rank, criteria=criteria
    )


def test_sensitivity_is_deterministic() -> None:
    scores = (place("a", 1, 10, 9), place("b", 2, 6, 7), place("c", 3, 2, 3), place("d", 4, 0, 1))
    assert analyze_sensitivity(scores, seed=7) == analyze_sensitivity(scores, seed=7)
    assert analyze_sensitivity(scores, seed=7)[0].top_three_frequency == 1


def test_sensitivity_requires_specified_minimum() -> None:
    with pytest.raises(ValueError, match="1,000"):
        analyze_sensitivity((place("a", 1, 10, 10),), simulations=999)
