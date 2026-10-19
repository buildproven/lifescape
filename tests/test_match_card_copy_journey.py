"""Saved match cards drop legacy copy after reload (BUI-1401; PRD G3).

Run this file on its own: concurrent Playwright runs time out.
"""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright
from test_match_card_journey import find_places, keep, running_app, watch_errors

LEGACY = """() => {
    const saved = JSON.parse(localStorage.getItem('lifescape.scenario'));
    const legacy = item => {
        item.components = item.components.filter(c => c.field !== 'large_metro_suburb');
        item.components.push({
            field: 'large_metro_suburb', label: 'Large-metro suburb', unit: 'yes or no',
            candidate_value: 0, target_value: 0,
            matched_exemplar: { place_id: 'X', name: 'Traverse City, MI' },
        });
        const where = ' in Traverse City, MI.';
        item.reasons = [
            { field: 'large_metro_suburb',
              text: 'Large-metro suburb: No. Close to No in Traverse City, MI.' },
            { field: 'annual_snowfall_in',
              text: 'Annual snowfall: 67 in per year. Close to 67 in per year' + where },
        ];
        item.differences = [{ field: 'annual_precipitation_in',
            text: 'Annual precipitation: 50 in per year, versus 33 in per year' + where }];
    };
    saved.result.recommendations.forEach(legacy);
    saved.shortlist.forEach(entry => legacy(entry.recommendation));
    localStorage.setItem('lifescape.scenario', JSON.stringify(saved));
}"""


def test_g3_saved_cards_write_inches_per_year_and_no_close_to_yes_or_no(tmp_path: Path) -> None:
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
        page.locator(".step-link[data-step-target=shortlist]").click()
        cards = page.locator("#shortlist-list .match-card")
        cards.first.wait_for()

        assert cards.count() == 2
        for index in range(cards.count()):
            text = cards.nth(index).inner_text()
            assert " in per year" not in text
            assert "Close to No" not in text and "Close to Yes" not in text
            assert "Not a large-metro suburb, like Traverse City, MI." in text
            assert "67 inches per year" in text
            assert "50 inches per year" in text
        assert errors == []
        browser.close()
