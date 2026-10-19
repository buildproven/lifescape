"""Steps 1 and 4 preview what verification checks (BUI-1398; PRD G4, G5).

Run this file on its own: concurrent Playwright runs time out.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import Page, sync_playwright
from test_match_card_journey import find_places, keep, running_app, watch_errors

PREVIEW = "[data-verification-preview]"


def critical_names(url: str) -> list[str]:
    with urlopen(f"{url}/api/bootstrap") as response:
        details = json.load(response)["metric_details"]
    names = [metric["name"] for metric in details if metric["critical"]]
    assert names
    return names


def visible_preview(page: Page) -> str:
    preview = page.locator(f"{PREVIEW}:visible")
    assert preview.count() == 1
    return preview.inner_text()


def test_req_verify_1_step_one_preview_names_critical_facts(tmp_path: Path) -> None:
    with running_app(tmp_path / "output") as url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = watch_errors(page)
        page.goto(url)
        page.get_by_role("heading", name="Where might you want to live?").wait_for()

        text = visible_preview(page)
        for name in critical_names(url):
            assert name in text
        assert "never guesses a missing value" in text
        assert "unknowns" in text.lower() and "visible" in text.lower()
        assert errors == []
        browser.close()


def test_req_verify_2_shortlist_step_shows_same_preview(tmp_path: Path) -> None:
    with running_app(tmp_path / "output") as url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = watch_errors(page)
        page.goto(url)
        step_one = visible_preview(page)
        find_places(page)
        keep(page, 2)
        page.get_by_role("button", name="Review shortlist").click()
        page.locator("#shortlist-list .match-card").first.wait_for()

        text = visible_preview(page)
        assert text == step_one
        for name in critical_names(url):
            assert name in text
        assert page.locator("[data-stage=shortlist] " + PREVIEW).is_visible()
        assert errors == []
        browser.close()


def test_req_verify_3_preview_is_leads_only_with_no_town_values(tmp_path: Path) -> None:
    with running_app(tmp_path / "output") as url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 844})
        errors = watch_errors(page)
        page.goto(url)
        page.get_by_role("heading", name="Where might you want to live?").wait_for()

        text = visible_preview(page)
        assert "discovery leads" in text and "not verified evidence" in text
        stripped = text
        for name in critical_names(url):
            stripped = stripped.replace(name, "")
        assert not re.search(r"\d", stripped)
        assert not re.search(r"pass|fail|score", stripped, re.IGNORECASE)
        assert page.evaluate(
            "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
        )
        assert errors == []
        browser.close()


def test_req_verify_4_fresh_load_hides_shortlist_and_empty_state(tmp_path: Path) -> None:
    with running_app(tmp_path / "output") as url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = watch_errors(page)
        page.goto(url)
        page.get_by_role("heading", name="Where might you want to live?").wait_for()

        assert page.locator("[data-step-target=shortlist]").is_disabled()
        assert not page.locator("[data-stage=shortlist]").is_visible()
        assert not page.get_by_text("Nothing kept yet").first.is_visible()
        assert errors == []
        browser.close()
