"""Straight-line miles read the same in Python and the browser (BUI-1448; PRD G3).

Found by keel while building BUI-1446: Python wrote "1,234.0 straight-line miles" but
`formatValue` in app.js used `toFixed(1)` and wrote "1234.0 straight-line miles".

The browser test starts a Playwright page; run this file on its own.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright
from test_match_card_journey import running_app, watch_errors

from lifescape.discovery import format_value

CASES = [
    (1234.0, "1,234.0 straight-line miles"),
    (1000.0, "1,000.0 straight-line miles"),
    (999.96, "1,000.0 straight-line miles"),
    (999.9, "999.9 straight-line miles"),
    (12345.67, "12,345.7 straight-line miles"),
    (1.0, "1 straight-line mile"),
    # Playwright sends Python 0.0 to the page as -0, so this also covers JSON "-0.0".
    (0.0, "0.0 straight-line miles"),
    # Exact binary ties: Python rounds half to even, so the browser must too.
    (0.25, "0.2 straight-line miles"),
    (1234.25, "1,234.2 straight-line miles"),
    (0.75, "0.8 straight-line miles"),
]


@pytest.mark.parametrize(("value", "expected"), CASES)
def test_bui_1448_python_straight_line_miles_format(value: float, expected: str) -> None:
    assert format_value("straight-line miles", value) == expected


def test_bui_1448_js_straight_line_miles_match_python(tmp_path: Path) -> None:
    with running_app(tmp_path / "output") as url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        errors = watch_errors(page)
        page.goto(url)
        page.get_by_role("heading", name="Where might you want to live?").wait_for()
        rendered = page.evaluate(
            "values => values.map((value) => formatValue('straight-line miles', value))",
            [value for value, _ in CASES],
        )
        browser.close()

    assert rendered == [format_value("straight-line miles", value) for value, _ in CASES]
    assert rendered == [expected for _, expected in CASES]
    assert errors == []
