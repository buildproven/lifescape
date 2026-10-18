"""Match cards show a feel summary and a band, not a percentage (BUI-1397; PRD G3, FR9).

Run this file on its own: concurrent Playwright runs time out.
"""

from __future__ import annotations

import re
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
import uvicorn
from playwright.sync_api import Page, sync_playwright

from lifescape.web import create_app

VIEWPORTS = [{"width": 390, "height": 844}, {"width": 1440, "height": 1000}]
BANDS = {"Strong", "Good", "Possible"}


def contrast_ratio(foreground: tuple[float, ...], background: tuple[float, ...]) -> float:
    def luminance(color: tuple[float, ...]) -> float:
        linear = [
            c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
            for c in (channel / 255 for channel in color)
        ]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    lighter, darker = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


@contextmanager
def running_app(output_dir: Path) -> Iterator[str]:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(create_app(output_dir), host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        if not thread.is_alive():
            raise RuntimeError("local app server stopped during startup")
        time.sleep(0.05)
    else:
        raise RuntimeError("local app server did not start")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def watch_errors(page: Page) -> list[str]:
    errors: list[str] = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    return errors


def fits_viewport(page: Page) -> bool:
    return page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    )


def find_places(page: Page) -> None:
    page.get_by_label("A town you already like").fill("Traverse City")
    page.get_by_role("button", name="Use as example").first.click()
    page.get_by_text("Comparing 14 qualities.").wait_for()
    page.get_by_role("button", name="Find places").click()
    page.locator("#match-list .match-card").first.wait_for()


def keep(page: Page, count: int) -> None:
    for _ in range(count):
        page.locator(
            "#match-list .decision-button[data-decision=keep][aria-pressed=false]"
        ).first.click()


def score_contrast(page: Page, selector: str) -> float:
    colors = page.locator(selector).first.evaluate(
        """element => {
            const parse = value => (value.match(/[\\d.]+/g) || []).map(Number).slice(0, 3);
            const style = getComputedStyle(element);
            return [parse(style.color), parse(style.backgroundColor)];
        }"""
    )
    return contrast_ratio(tuple(colors[0]), tuple(colors[1]))


@pytest.mark.parametrize("viewport", VIEWPORTS)
def test_req_card_3_and_7_card_order_band_and_fit(tmp_path: Path, viewport: dict[str, int]) -> None:
    with running_app(tmp_path / "output") as url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport=viewport)
        errors = watch_errors(page)
        page.goto(url)
        find_places(page)

        cards = page.locator("#match-list .match-card")
        assert cards.count() == 10
        for index in range(cards.count()):
            card = cards.nth(index)
            children = card.locator(":scope > *").evaluate_all(
                "els => els.map(el => [el.className, el.textContent.trim()])"
            )
            summary = next(i for i, (cls, _) in enumerate(children) if "feel-summary" in cls)
            why = next(i for i, (_, text) in enumerate(children) if text == "Why it appeared")
            reasons = next(i for i, (cls, _) in enumerate(children) if "reason-list" in cls)
            trade_off = next(
                i for i, (_, text) in enumerate(children) if text == "Biggest trade-off"
            )
            assert summary < why < reasons < trade_off
            assert card.locator(".feel-summary").inner_text().strip()
            score = card.locator(".match-score")
            band = score.inner_text().strip()
            assert band in BANDS
            name = score.get_attribute("aria-label") or ""
            assert band in name and "discovery" in name
            assert not re.search(r"\d+% match", card.inner_text())
            assert "%" not in score.inner_text()

        assert fits_viewport(page)
        assert score_contrast(page, "#match-list .match-score") >= 4.5
        assert errors == []
        browser.close()


@pytest.mark.parametrize("viewport", VIEWPORTS)
def test_req_card_4_and_5_compare_snapshot_checklist_and_labels(
    tmp_path: Path, viewport: dict[str, int]
) -> None:
    with running_app(tmp_path / "output") as url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport=viewport)
        errors = watch_errors(page)
        page.goto(url)
        find_places(page)

        assert page.get_by_text("Discovery, not proof").is_visible()
        first = page.locator("#match-list .match-card").first
        first.get_by_role("button", name="Why this place?").click()
        assert first.get_by_text("not verified evidence").first.is_visible()
        assert first.get_by_text("Discovery data").first.is_visible()

        keep(page, 2)
        page.get_by_role("button", name="Review shortlist").click()

        row = page.locator("#compare-table tr", has=page.locator("th", has_text="Discovery match"))
        cells = row.locator("td").all_inner_texts()
        assert len(cells) == 2
        assert all(cell.strip() in BANDS for cell in cells)
        assert "%" not in row.inner_text()
        snapshot_scores = page.locator("#shortlist-list .match-card .match-score")
        assert snapshot_scores.count() == 2
        assert all(text.strip() in BANDS for text in snapshot_scores.all_inner_texts())
        assert not re.search(r"\d+% match", page.locator("#shortlist-list").inner_text())

        page.get_by_role("button", name="Verify finalists").click()
        with page.expect_download() as download:
            page.get_by_role("button", name="Download research checklist").click()
        text = Path(download.value.path()).read_text(encoding="utf-8")
        lines = [line for line in text.splitlines() if line.startswith("Discovery match:")]
        assert len(lines) == 2
        for line in lines:
            assert re.fullmatch(
                r"Discovery match: (Strong|Good|Possible) \(not verified evidence\)", line
            )
        assert fits_viewport(page)
        assert errors == []
        browser.close()


@pytest.mark.parametrize("viewport", VIEWPORTS)
def test_req_card_6_snapshot_without_new_fields_renders_without_percentage(
    tmp_path: Path, viewport: dict[str, int]
) -> None:
    with running_app(tmp_path / "output") as url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport=viewport)
        errors = watch_errors(page)
        page.goto(url)
        find_places(page)
        keep(page, 2)
        page.evaluate(
            """() => {
                const saved = JSON.parse(localStorage.getItem('lifescape.scenario'));
                const strip = item => { delete item.match_band; delete item.feel_summary; };
                saved.result.recommendations.forEach(strip);
                saved.shortlist.forEach(entry => strip(entry.recommendation));
                localStorage.setItem('lifescape.scenario', JSON.stringify(saved));
            }"""
        )
        page.reload()
        page.get_by_role("heading", name="Where might you want to live?").wait_for()
        page.locator(".step-link[data-step-target=shortlist]").click()
        cards = page.locator("#shortlist-list .match-card")
        cards.first.wait_for()

        assert cards.count() == 2
        assert page.locator("#shortlist-list .feel-summary").count() == 0
        for index in range(cards.count()):
            card = cards.nth(index)
            assert card.locator(".reason-list li").count() >= 2
            assert "%" not in card.locator("header").inner_text()
            assert not re.search(r"\d+% match", card.inner_text())
        assert "%" not in page.locator("#compare-table tr", has_text="Discovery match").inner_text()
        assert fits_viewport(page)
        assert errors == []
        browser.close()
