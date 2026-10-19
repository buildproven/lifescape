from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

KEEL_JSON = Path(__file__).resolve().parents[1] / "keel.json"


@pytest.fixture(scope="module")
def keel_config() -> dict[str, Any]:
    with KEEL_JSON.open(encoding="utf-8") as handle:
        config: dict[str, Any] = json.load(handle)
    return config


def test_keel_config_omits_held_out_author_tier(keel_config: dict[str, Any]) -> None:
    assert "heldOutAuthorTier" not in keel_config


def test_keel_config_omits_daily_budget(keel_config: dict[str, Any]) -> None:
    assert "dailyBudgetUsd" not in keel_config


def test_keel_config_keeps_expected_values(keel_config: dict[str, Any]) -> None:
    assert keel_config["checks"] == {
        "lint": "make lint",
        "test": "make test",
        "typecheck": "uv run --extra dev mypy src",
    }
    assert keel_config["testCommand"] == "make test"
    assert keel_config["linearProject"] == "lifescape"
    assert keel_config["install"] == "uv sync --frozen --extra dev && npm ci"
    assert keel_config["review"] is True
