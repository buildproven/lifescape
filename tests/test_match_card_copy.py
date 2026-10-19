"""Match-card reason copy reads naturally (BUI-1401; PRD G3).

Manual run on PR #52 showed "Annual snowfall: 67 in per year" and
"Large-metro suburb: No. Close to No in Traverse City, MI."
"""

from __future__ import annotations

import pytest

from lifescape.discovery import (
    DiscoveryService,
    PlaceCatalog,
    SearchProfile,
    format_value,
    load_catalog,
)


@pytest.fixture(scope="module")
def traverse_cards() -> list[dict]:
    catalog: PlaceCatalog = load_catalog()
    traverse = catalog.lookup("Traverse City, MI")[0]
    profile = SearchProfile(exemplars=(traverse.place_id,))
    return DiscoveryService(catalog).search(profile, limit=50)["recommendations"]


def card_texts(cards: list[dict]) -> list[str]:
    return [item["text"] for card in cards for item in (*card["reasons"], *card["differences"])]


def test_g3_inches_per_year_unit_is_written_out() -> None:
    assert format_value("inches per year", 67.2) == "67 inches per year"


def test_g3_yes_no_values_are_not_phrased_as_close_to_yes_or_no(
    traverse_cards: list[dict],
) -> None:
    texts = card_texts(traverse_cards)

    assert texts
    assert not [t for t in texts if " in per year" in t]
    assert not [t for t in texts if "Close to No" in t or "Close to Yes" in t]
    assert any("inches per year" in t for t in texts)
    assert any(t == "Not a large-metro suburb, like Traverse City, MI." for t in texts)
