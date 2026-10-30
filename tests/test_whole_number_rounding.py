"""Whole-number values round half to even in the browser, as Python does (BUI-1487; PRD G3).

Python writes whole numbers with f"{value:,.0f}" (ties to even), but the `whole` formatter in
app.js rounded ties away from zero, so a match card re-rendered in the browser could read
"3 miles" where the server wrote "2 miles".

The browser test starts a Playwright page; run this file on its own.
"""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright
from test_match_card_journey import running_app, watch_errors

from lifescape.discovery import format_value

# REQ-FMT-1: exact .5 ties round half to even.
TIES = [0.5, 1.5, 2.5, 1234.5, -2.5, 999.5, -0.5]
# REQ-FMT-2: values that are not ties stay grouped with no decimals.
NON_TIES = [0.4, 0.6, 1.0, 2.6, 1234.4, 1234567.6, -2.6, 12345.0]
UNITS = [
    "people per square mile",
    "days per year",
    "inches per year",
    "feet above sea level",
    "miles",
    "count",
]


def render(tmp_path: Path, values: list[float]) -> tuple[list[str], dict[str, list[str]]]:
    with running_app(tmp_path / "output") as url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        errors = watch_errors(page)
        page.goto(url)
        page.get_by_role("heading", name="Where might you want to live?").wait_for()
        bare = page.evaluate("values => values.map((value) => whole.format(value))", values)
        by_unit = page.evaluate(
            "([units, values]) => Object.fromEntries(units.map((unit) =>"
            " [unit, values.map((value) => formatValue(unit, value))]))",
            [UNITS, values],
        )
        browser.close()
    assert errors == []
    return bare, by_unit


def test_bui_1487_req_fmt_1_and_2_whole_matches_python(tmp_path: Path) -> None:
    values = TIES + NON_TIES
    bare, by_unit = render(tmp_path, values)

    assert bare == [f"{value:,.0f}" for value in values]
    assert bare[: len(TIES)] == ["0", "2", "2", "1,234", "-2", "1,000", "-0"]
    assert bare[len(TIES) :] == ["0", "1", "1", "3", "1,234", "1,234,568", "-3", "12,345"]
    for unit in UNITS:
        assert by_unit[unit] == [format_value(unit, value) for value in values], unit
