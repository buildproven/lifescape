"""USD values round half to even in the browser, as Python does (BUI-1553; PRD G3).

Python writes USD with f"${value:,.0f}" (ties to even, "$-2" for negatives), but the `money`
formatter in app.js used Intl default rounding (ties away from zero) and wrote "-$2", so a
match card re-rendered in the browser could read "$3" where the server wrote "$2".

The browser test starts a Playwright page; run this file on its own.
"""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright
from test_match_card_journey import running_app, watch_errors

from lifescape.discovery import format_value

# REQ-USD-1: exact .5 ties round half to even.
TIES = [0.5, 1.5, 2.5, 310000.5, 310001.5, 999.5, -2.5, -0.5]
# REQ-USD-2: values that are not ties stay grouped with no decimals; negatives read "$-N".
NON_TIES = [0.4, 1.0, 2.6, 310000.4, 1234567.6, -2.6, 12345.0]


def render(tmp_path: Path, values: list[float]) -> tuple[list[str], str]:
    with running_app(tmp_path / "output") as url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        errors = watch_errors(page)
        page.goto(url)
        page.get_by_role("heading", name="Where might you want to live?").wait_for()
        rendered = page.evaluate(
            "values => values.map((value) => formatValue('USD', value))", values
        )
        budget = page.evaluate(
            "() => { const input = $('#budget'); input.value = '1225000';"
            " input.dispatchEvent(new Event('input'));"
            " return $('#budget-output').textContent; }"
        )
        browser.close()
    assert errors == []
    return rendered, budget


def test_bui_1553_req_usd_1_and_2_browser_usd_matches_python(tmp_path: Path) -> None:
    values = TIES + NON_TIES
    rendered, budget = render(tmp_path, values)

    assert rendered == [format_value("USD", value) for value in values]
    assert rendered[: len(TIES)] == [
        "$0",
        "$2",
        "$2",
        "$310,000",
        "$310,002",
        "$1,000",
        "$-2",
        "$-0",
    ]
    assert rendered[len(TIES) :] == [
        "$0",
        "$1",
        "$3",
        "$310,000",
        "$1,234,568",
        "$-3",
        "$12,345",
    ]
    # The budget slider readout uses the same USD format.
    assert budget == "$1,225,000" == format_value("USD", 1225000)
