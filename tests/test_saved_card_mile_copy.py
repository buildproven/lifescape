"""Saved match cards write "1 mile" after reload (BUI-1447; PRD G3).

Snapshots saved before BUI-1446 keep reason copy such as
"Distance to ocean or Great Lake: 1 miles." and "1.0 straight-line miles".

Run this file on its own: concurrent Playwright runs time out.
"""

from __future__ import annotations

import re
from pathlib import Path

from playwright.sync_api import sync_playwright
from test_match_card_journey import find_places, keep, running_app, watch_errors

LEGACY = """() => {
    const saved = JSON.parse(localStorage.getItem('lifescape.scenario'));
    const legacy = item => {
        const where = ' in Traverse City, MI.';
        item.reasons = [
            { field: 'distance_to_water_mi',
              text: 'Distance to ocean or Great Lake: 1 miles. Close to 1 miles' + where },
            { field: 'hospital_distance_mi',
              text: 'Hospital distance: 1.0 straight-line miles to Munson. Close to 11.0 '
                + 'straight-line miles' + where },
        ];
        item.differences = [{ field: 'distance_to_water_mi',
            text: 'Distance to ocean or Great Lake: 1 miles, versus 21 miles' + where }];
    };
    saved.result.recommendations.forEach(legacy);
    saved.shortlist.forEach(entry => legacy(entry.recommendation));
    localStorage.setItem('lifescape.scenario', JSON.stringify(saved));
}"""

COPY_CASES = [
    ["Distance: 1 miles.", "Distance: 1 mile."],
    ["Close to 1 miles in X.", "Close to 1 mile in X."],
    ["Distance: 11 miles, versus 1,001 miles.", "Distance: 11 miles, versus 1,001 miles."],
    ["Distance: 21 miles.", "Distance: 21 miles."],
    ["Hospital: 1.0 straight-line miles to A.", "Hospital: 1 straight-line mile to A."],
    ["Hospital: 0.1 straight-line miles.", "Hospital: 0.1 straight-line miles."],
    ["Hospital: 11.0 straight-line miles.", "Hospital: 11.0 straight-line miles."],
    ["Hospital: 1.05 straight-line miles.", "Hospital: 1.05 straight-line miles."],
    ["Distance: 1 mile.", "Distance: 1 mile."],
]


def test_bui_1447_saved_cards_write_1_mile_after_reload(tmp_path: Path) -> None:
    with running_app(tmp_path / "output") as url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = watch_errors(page)
        page.goto(url)
        find_places(page)
        keep(page, 2)
        page.evaluate(LEGACY)
        page.reload()
        page.get_by_role("heading", name="Where might you want to live?").wait_for()
        rewritten = page.evaluate(
            "cases => cases.map(([text]) => currentCopy({ field: 'x', text }, { components: [] }))",
            COPY_CASES,
        )
        page.locator(".step-link[data-step-target=shortlist]").click()
        cards = page.locator("#shortlist-list .match-card")
        cards.first.wait_for()
        texts = [cards.nth(index).inner_text() for index in range(cards.count())]
        browser.close()

    assert rewritten == [expected for _, expected in COPY_CASES]
    assert len(texts) == 2
    for text in texts:
        assert not re.search(r"(?<![\d.,])1(\.0)? (straight-line )?miles", text)
        assert "Distance to ocean or Great Lake: 1 mile. Close to 1 mile in Traverse City" in text
        assert "1 straight-line mile to Munson. Close to 11.0 straight-line miles" in text
        assert "Distance to ocean or Great Lake: 1 mile, versus 21 miles" in text
    assert errors == []
