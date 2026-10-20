"""The shortlist comparison note names every source the table shows (BUI-1445; PRD G3).

Manual run on main (a9dd135): the "Side by side" note read "Census and NOAA climate values for
the towns you kept", while the table also listed elevation, water and metro distance (place
character) and the nearest emergency hospital (CMS).

The browser test starts a Playwright page; run this file on its own.
"""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright
from test_match_card_journey import find_places, keep, running_app, watch_errors

NON_CENSUS_NOAA_ROWS = (
    "Elevation",
    "Distance to ocean or Great Lake",
    "Distance to a large metro",
    "Large-metro suburb",
    "Nearest emergency hospital",
)


def test_bui_1445_compare_note_names_place_character_and_hospital_sources(
    tmp_path: Path,
) -> None:
    with running_app(tmp_path / "output") as url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = watch_errors(page)
        page.goto(url)
        find_places(page)
        keep(page, 2)
        page.get_by_role("button", name="Review shortlist").click()
        page.locator("#compare-section").wait_for()

        rows = page.locator("#compare-table th[scope=row]").all_inner_texts()
        for label in NON_CENSUS_NOAA_ROWS:
            assert label in rows

        note = page.locator("#compare-note").inner_text()
        assert "Census and NOAA climate values" not in note
        for source in ("Census", "NOAA climate", "place character", "emergency hospital"):
            assert source in note
        assert "Lower or higher is not better" in note
        assert "Hand-added" not in note

        page.get_by_label("Add a town yourself").fill("Marquette, MI")
        page.get_by_role("button", name="Add manually").first.click()
        note = page.locator("#compare-note").inner_text()
        assert "place character" in note
        assert "Hand-added towns (1) have no discovery data and are left out." in note
        assert errors == []
        browser.close()
