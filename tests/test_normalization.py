import pytest

from retirement_engine.models import Direction
from retirement_engine.normalization import normalize_values, percentile


def test_directionality_and_winsorization() -> None:
    values = {"a": 0.0, "b": 50.0, "c": 1000.0}
    higher = normalize_values(values, Direction.HIGHER)
    lower = normalize_values(values, Direction.LOWER)
    assert higher["a"] == 0
    assert higher["c"] == 10
    assert lower["a"] == 10
    assert lower["c"] == 0


def test_equal_values_are_neutral() -> None:
    assert normalize_values({"a": 3, "b": 3}, Direction.HIGHER) == {"a": 5, "b": 5}


def test_empty_percentile_is_invalid() -> None:
    with pytest.raises(ValueError):
        percentile([], 0.5)
