"""One mile is written in the singular on match cards (BUI-1446; PRD G3).

Manual run on main (198ac6d), Traverse City example, Petoskey, MI card:
"Distance to ocean or Great Lake: 1 miles."

The browser test starts a Playwright page; run this file on its own.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright
from test_match_card_journey import running_app, watch_errors

from lifescape.discovery import (
    DiscoveryService,
    SearchProfile,
    format_value,
    load_catalog,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1.0, "1 mile"),
        (0.6, "1 mile"),
        (1.4, "1 mile"),
        (0.4, "0 miles"),
        (1.6, "2 miles"),
        (1234.0, "1,234 miles"),
    ],
)
def test_bui_1446_rounded_one_mile_is_singular(value: float, expected: str) -> None:
    assert format_value("miles", value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1.0, "1 straight-line mile"),
        (0.96, "1 straight-line mile"),
        (1.04, "1 straight-line mile"),
        (0.94, "0.9 straight-line miles"),
        (1.06, "1.1 straight-line miles"),
        (0.0, "0.0 straight-line miles"),
    ],
)
def test_bui_1446_rounded_one_straight_line_mile_is_singular(value: float, expected: str) -> None:
    assert format_value("straight-line miles", value) == expected


def test_bui_1446_petoskey_card_writes_1_mile_not_1_miles() -> None:
    catalog = load_catalog()
    traverse = catalog.lookup("Traverse City, MI")[0]
    cards = DiscoveryService(catalog).search(
        SearchProfile(exemplars=(traverse.place_id,)), limit=50
    )["recommendations"]
    texts = [item["text"] for card in cards for item in (*card["reasons"], *card["differences"])]

    assert "Distance to ocean or Great Lake: 1 mile." in " ".join(texts)
    assert not [text for text in texts if "1 miles" in text.replace(",", "")]


JS_CASES = [
    ["miles", 1.0, "1 mile"],
    ["miles", 0.6, "1 mile"],
    ["miles", 1.6, "2 miles"],
    ["miles", 0.4, "0 miles"],
    ["miles", 1234, "1,234 miles"],
    ["straight-line miles", 1.0, "1 straight-line mile"],
    ["straight-line miles", 0.96, "1 straight-line mile"],
    ["straight-line miles", 0.94, "0.9 straight-line miles"],
    ["straight-line miles", 1.06, "1.1 straight-line miles"],
]


def test_bui_1446_js_format_value_writes_singular_mile(tmp_path: Path) -> None:
    with running_app(tmp_path / "output") as url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        errors = watch_errors(page)
        page.goto(url)
        page.get_by_role("heading", name="Where might you want to live?").wait_for()
        rendered = page.evaluate(
            "cases => cases.map(([unit, value]) => formatValue(unit, value))", JS_CASES
        )
        browser.close()

    assert rendered == [expected for _, _, expected in JS_CASES]
    assert errors == []
