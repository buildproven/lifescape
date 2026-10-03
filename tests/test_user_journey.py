from __future__ import annotations

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


def contrast_ratio(
    foreground: tuple[int, int, int],
    background: tuple[int, int, int],
    alpha: float = 1,
) -> float:
    composited = tuple(
        (alpha * foreground_channel + (1 - alpha) * background_channel) / 255
        for foreground_channel, background_channel in zip(foreground, background, strict=True)
    )
    normalized_background = tuple(channel / 255 for channel in background)

    def luminance(color: tuple[float, float, float]) -> float:
        linear = tuple(
            channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
            for channel in color
        )
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    foreground_luminance = luminance(composited)
    background_luminance = luminance(normalized_background)
    return (max(foreground_luminance, background_luminance) + 0.05) / (
        min(foreground_luminance, background_luminance) + 0.05
    )


@contextmanager
def running_app(
    output_dir: Path,
    *,
    hosted_demo: bool = False,
) -> Iterator[str]:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(
                output_dir,
                hosted_demo=hosted_demo,
                hosted_runs_enabled=True if hosted_demo else None,
            ),
            host="127.0.0.1",
            port=port,
            log_level="error",
        )
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


VIEWPORTS = [{"width": 390, "height": 844}, {"width": 1440, "height": 1000}]


def start_search(page: Page, example: str = "Traverse City", *, limits: bool = False) -> None:
    """Choose an example town; optionally continue to the boundaries step."""
    page.get_by_label("A town you already like").fill(example)
    page.get_by_role("button", name="Use as example").first.click()
    page.get_by_text("Comparing 6 qualities.").wait_for()
    if limits:
        page.get_by_role("button", name="Set limits first (price, regions)").click()


def find_places(page: Page) -> None:
    page.get_by_role("button", name="Find places").click()
    page.locator(".match-card").first.wait_for()


def keep(page: Page, count: int) -> None:
    for _ in range(count):
        page.locator(
            "#match-list .decision-button[data-decision=keep][aria-pressed=false]"
        ).first.click()


def fits_viewport(page: Page) -> bool:
    return page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    )


def watch_errors(page: Page) -> list[str]:
    errors: list[str] = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    return errors


@pytest.mark.parametrize("viewport", VIEWPORTS)
def test_discovery_journey_from_example_town_to_recovered_shortlist(
    tmp_path: Path, viewport: dict[str, int]
) -> None:
    with running_app(tmp_path / "output") as url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport=viewport)
        errors = watch_errors(page)
        page.goto(url)

        page.get_by_role("heading", name="Where might you want to live?").wait_for()
        # FR1: Find places is the primary action; CSV import is not on the first screen.
        assert page.get_by_role("button", name="Find places").is_disabled()
        assert page.get_by_text("Pick a town you like or a style above to begin.").is_visible()
        assert page.get_by_text("checks your finalists against evidence").is_visible()
        assert page.get_by_role("button", name="Advanced evidence import").is_hidden()
        start_search(page, limits=True)
        page.get_by_label("Median home value no more than").fill("500000")
        find_places(page)

        assert page.locator(".match-card").count() == 10
        first = page.locator(".match-card").first
        assert first.locator(".reason-list li").count() >= 2
        assert first.locator(".trade-off").inner_text().strip()
        assert page.get_by_text("Discovery, not proof").is_visible()
        first.get_by_role("button", name="Why this place?").click()
        assert first.get_by_text("not verified evidence").first.is_visible()
        assert first.get_by_text("Discovery data").first.is_visible()
        assert fits_viewport(page)

        keep(page, 3)
        assert page.locator("#kept-count").inner_text() == "3"
        kept_names = page.locator(
            "#match-list .match-card:has(.decision-button[aria-pressed=true]) h3"
        ).all_inner_texts()
        assert len(kept_names) == 3

        page.reload()
        page.get_by_role("heading", name="Where might you want to live?").wait_for()
        page.locator("#exemplar-chips").get_by_text("Traverse City, MI").wait_for()
        page.locator(".step-link[data-step-target=shortlist]").click()
        page.locator("#shortlist-list .match-card").first.wait_for()
        recovered = page.locator("#shortlist-list .match-card h3").all_inner_texts()
        assert recovered == kept_names
        assert page.locator("#shortlist-count").inner_text() == "3"
        assert page.get_by_text("Your shortlist is ready for verification.").is_visible()
        assert fits_viewport(page)
        assert errors == []
        browser.close()


def test_discovery_rerun_explains_movement_and_replaces_rejected_towns(tmp_path: Path) -> None:
    with running_app(tmp_path / "output") as url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport=VIEWPORTS[1])
        errors = watch_errors(page)
        page.goto(url)
        start_search(page)
        find_places(page)
        first_name = page.locator(".match-card h3").first.inner_text()
        page.locator(".match-card").first.get_by_role("button", name="Not for me").click()

        page.get_by_role("button", name="Back").click()
        page.get_by_role("button", name="Find places").click()
        page.locator(".match-card .movement:not(:empty)").first.wait_for()

        names = page.locator(".match-card h3").all_inner_texts()
        assert first_name not in names
        assert len(names) == 10
        assert "towns marked Not for me" in page.locator("#match-list").inner_text()
        assert errors == []
        browser.close()


def test_discovery_blocks_search_until_two_qualities_exist_and_flags_small_towns(
    tmp_path: Path,
) -> None:
    with running_app(tmp_path / "output") as url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport=VIEWPORTS[1])
        page.goto(url)
        page.get_by_label("A town you already like").fill("Abanda")
        page.get_by_text("Examples need a population of 2,500 or more.").first.wait_for()
        assert page.get_by_role("button", name="Use as example").first.is_disabled()
        assert page.get_by_role("button", name="Find places").is_disabled()
        browser.close()


def test_discovery_recovers_from_a_malformed_saved_search(tmp_path: Path) -> None:
    def banner_says(page: Page, text: str) -> None:
        page.wait_for_function(
            "text => document.querySelector('#scenario-banner').textContent"
            ".toLowerCase().includes(text)",
            arg=text,
        )

    with running_app(tmp_path / "output") as url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport=VIEWPORTS[1])
        errors = watch_errors(page)
        page.goto(url)
        page.evaluate(
            "localStorage.setItem('lifescape.scenario', "
            '\'{"schema_version": 1, "profile": {}, "shortlist": "oops"}\')'
        )
        page.reload()
        banner_says(page, "could not be opened")
        assert page.evaluate("localStorage.getItem('lifescape.scenario.backup')") is not None
        assert page.locator("#quality-list .quality-row").count() == 6
        page.locator("#scenario-banner").get_by_role("button", name="Start fresh").click()
        assert page.evaluate("localStorage.getItem('lifescape.scenario')") is None
        page.evaluate("localStorage.setItem('lifescape.scenario', '{not json')")
        page.reload()
        banner_says(page, "could not be opened")
        page.evaluate(
            "localStorage.setItem('lifescape.scenario', JSON.stringify({schema_version: 2}))"
        )
        page.reload()
        banner_says(page, "unsupported schema version")
        assert errors == []
        browser.close()


def test_discovery_rejects_a_saved_snapshot_missing_recommendation_fields(
    tmp_path: Path,
) -> None:
    with running_app(tmp_path / "output") as url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport=VIEWPORTS[1])
        errors = watch_errors(page)
        page.goto(url)
        start_search(page)
        find_places(page)
        page.evaluate(
            """() => {
                const saved = JSON.parse(localStorage.getItem('lifescape.scenario'));
                delete saved.result.recommendations[0].reasons;
                localStorage.setItem('lifescape.scenario', JSON.stringify(saved));
            }"""
        )
        page.reload()
        page.wait_for_function(
            "() => document.querySelector('#scenario-banner').textContent"
            ".includes('invalid recommendation snapshot')"
        )
        assert page.locator("#quality-list .quality-row").count() == 6
        assert errors == []
        browser.close()


def test_discovery_search_can_be_retried_after_a_failure(tmp_path: Path) -> None:
    with running_app(tmp_path / "output") as url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport=VIEWPORTS[1])
        page.goto(url)
        start_search(page)
        page.route("**/api/place-recommendations", lambda route: route.abort())
        page.get_by_role("button", name="Find places").click()
        page.get_by_text("Search could not run").wait_for()
        assert page.get_by_role("button", name="Find places").is_enabled()
        page.unroute("**/api/place-recommendations")
        page.get_by_role("button", name="Find places").click()
        page.locator(".match-card").first.wait_for()
        assert page.locator("#match-list .match-card").count() == 10
        browser.close()


def test_discovery_never_lists_one_town_twice_on_the_shortlist(tmp_path: Path) -> None:
    with running_app(tmp_path / "output") as url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport=VIEWPORTS[1])
        page.goto(url)
        start_search(page)
        find_places(page)
        first = page.locator("#match-list .match-card h3").first.inner_text()
        page.get_by_role("button", name="Review shortlist").click()
        page.get_by_label("Add a town yourself").fill(first)
        page.get_by_role("button", name="Add manually").first.click()
        page.get_by_text("Added by hand.").wait_for()
        page.locator(".step-link[data-step-target=matches]").click()
        page.locator("#match-list .decision-button[data-decision=keep]").first.click()

        assert page.locator("#shortlist-count").inner_text() == "1"
        assert (
            page.evaluate("JSON.parse(localStorage.getItem('lifescape.scenario')).shortlist.length")
            == 1
        )
        browser.close()


def test_discovery_rejected_town_chosen_as_example_does_not_break_search(
    tmp_path: Path,
) -> None:
    with running_app(tmp_path / "output") as url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport=VIEWPORTS[1])
        page.goto(url)
        start_search(page)
        find_places(page)
        rejected = page.locator("#match-list .match-card h3").first.inner_text()
        page.locator("#match-list .decision-button[data-decision=reject]").first.click()
        page.get_by_role("button", name="Back").click()
        page.get_by_role("button", name="Back").click()
        page.get_by_label("A town you already like").fill(rejected)
        page.get_by_role("button", name="Use as example").first.click()
        page.get_by_role("button", name="Find places").click()
        page.locator("#match-list .match-card").first.wait_for()

        assert rejected not in page.locator("#match-list .match-card h3").all_inner_texts()
        browser.close()


def test_discovery_keeps_an_older_catalog_snapshot_readable(tmp_path: Path) -> None:
    with running_app(tmp_path / "output") as url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport=VIEWPORTS[1])
        page.goto(url)
        start_search(page)
        find_places(page)
        keep(page, 1)
        page.evaluate(
            """() => {
                const saved = JSON.parse(localStorage.getItem('lifescape.scenario'));
                saved.result.catalog_version = 'older-catalog-v0';
                localStorage.setItem('lifescape.scenario', JSON.stringify(saved));
            }"""
        )
        page.reload()
        page.locator(".step-link[data-step-target=matches]").click()
        assert page.locator("#stale-banner").is_visible()
        assert "older-catalog-v0" in page.locator("#stale-copy").inner_text()
        assert page.locator("#match-list .match-card").count() == 10
        browser.close()


def test_discovery_adds_a_small_town_manually_and_exports_json(tmp_path: Path) -> None:
    with running_app(tmp_path / "output") as url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport=VIEWPORTS[1])
        page.goto(url)
        start_search(page)
        find_places(page)
        page.get_by_role("button", name="Review shortlist").click()
        page.get_by_label("Add a town yourself").fill("Abanda")
        page.get_by_role("button", name="Add manually").first.click()
        page.get_by_text("Added by hand.").wait_for()
        assert "under 2,500" in page.locator("#shortlist-list").inner_text()
        with page.expect_download() as download:
            page.get_by_role("button", name="Export search as JSON").click()
        assert download.value.suggested_filename == "lifescape-search.json"
        page.get_by_role("button", name="Start over").click()
        page.get_by_role("button", name="Confirm: clear my search and shortlist").click()
        page.get_by_role("heading", name="Where might you want to live?").wait_for()
        assert page.evaluate("localStorage.getItem('lifescape.scenario')") is None
        browser.close()


def test_evidence_handoff_runs_only_with_reviewed_evidence(tmp_path: Path) -> None:
    with running_app(tmp_path / "output") as url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport=VIEWPORTS[1])
        errors = watch_errors(page)
        page.goto(url)
        start_search(page)
        find_places(page)
        # Williamsburg, VA is the first match and also a synthetic benchmark town.
        keep(page, 1)
        page.get_by_role("button", name="Review shortlist").click()
        assert page.get_by_role("button", name="Verify finalists").is_disabled()
        page.get_by_label("Add a town yourself").fill("Abanda")
        page.get_by_role("button", name="Add manually").first.click()
        page.get_by_role("button", name="Verify finalists").click()

        page.get_by_role("heading", name="Test your finalists with evidence").wait_for()
        rows = page.locator(".handoff-row")
        assert rows.count() == 2
        assert "metrics provided" in rows.nth(0).inner_text()
        assert "No reviewed evidence for this town yet" in rows.nth(1).inner_text()
        rows.nth(1).locator("summary").click()
        assert rows.nth(1).locator(".metric-list li.is-absent").count() == 17
        assert rows.nth(1).locator(".metric-list .tag", has_text="Critical").count() >= 1
        # A discovery record alone cannot enable the comparison: only one town has evidence.
        assert page.get_by_role("button", name="Run comparison").is_disabled()
        assert "at least two" in page.locator("#action-hint").inner_text()
        page.locator(".step-link[data-step-target=shortlist]").click()
        page.get_by_label("Add a town yourself").fill("Lake Geneva")
        page.get_by_role("button", name="Add manually").first.click()
        page.get_by_role("button", name="Verify finalists").click()
        page.locator(".handoff-row").nth(2).wait_for()
        assert page.get_by_text("2 of 3 ready to compare").is_visible()
        page.get_by_role("button", name="Run comparison").click()

        page.locator("#result-lead h2").wait_for()
        assert page.locator("#synthetic-notice").is_visible()
        assert "synthetic" in page.locator("#synthetic-notice").inner_text()
        assert page.get_by_role("link", name="SQLite provenance").is_visible()
        assert errors == []
        browser.close()


def test_advanced_evidence_import_runs_without_discovery(tmp_path: Path) -> None:
    evidence = Path("data/benchmarks/evidence.csv").read_text(encoding="utf-8")
    mixed_evidence = evidence.replace(",true,", ",false,", 1)
    errors: list[str] = []
    with running_app(tmp_path / "output") as url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport=VIEWPORTS[0])
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.goto(url)
        page.get_by_role("heading", name="Where might you want to live?").wait_for()
        assert page.locator(".step-link[data-step-target=verify]").is_disabled()
        page.get_by_role("button", name="I already have a shortlist and reviewed evidence").click()
        page.get_by_role("heading", name="Test your finalists with evidence").wait_for()
        page.locator("#evidence-file").set_input_files(
            {"name": "oversized.csv", "mimeType": "text/csv", "buffer": b"x" * 5_000_001}
        )
        page.get_by_text("Evidence CSV exceeds the 5 MB local-app limit.").wait_for()
        page.locator("#evidence-file").set_input_files(
            {"name": "mixed.csv", "mimeType": "text/csv", "buffer": mixed_evidence.encode()}
        )
        page.wait_for_function(
            "() => document.querySelector('#dataset-meta').textContent.includes('mixed evidence')"
        )
        page.locator(".step-link[data-step-target=verify]").click()
        page.get_by_text("From your imported evidence").wait_for()
        for name in ("Williamsburg", "Lake Geneva"):
            page.locator(".town-row", has_text=name).locator("input").check(force=True)
        page.get_by_role("button", name="Run comparison").click()
        page.locator("#result-lead h2").wait_for()
        warning = (
            "This run contains synthetic values. Treat its results as test output, "
            "not purchase research."
        )
        assert warning in page.locator("#synthetic-notice").inner_text()
        assert errors == []
        browser.close()


def test_hosted_disclosure_survives_without_javascript(tmp_path: Path) -> None:
    with running_app(tmp_path / "output", hosted_demo=True) as url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(java_script_enabled=False, viewport={"width": 1440, "height": 1000})
        page.goto(f"{url}/demo")

        assert page.get_by_text("Finished synthetic demonstration").is_visible()
        assert page.get_by_text("Completed synthetic outcome").is_visible()
        assert page.get_by_text(
            "This finished run shows the shape of a Lifescape answer"
        ).is_visible()
        assert page.get_by_text("Your evidence and outputs stay on this computer.").count() == 0
        browser.close()


def test_landing_disclosures_survive_without_javascript(tmp_path: Path) -> None:
    with running_app(tmp_path / "output", hosted_demo=True) as url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(java_script_enabled=False, viewport={"width": 1440, "height": 1000})
        page.goto(url)

        assert page.get_by_role(
            "heading", name="From “maybe there” to a decision you can inspect."
        ).is_visible()
        assert page.get_by_text("Synthetic example", exact=True).is_visible()
        assert page.get_by_text(
            "The hosted site accepts no inputs: it explains the method and shows a finished "
            "synthetic example."
        ).is_visible()
        assert page.get_by_text("Use the web demo to learn it.").is_visible()
        browser.close()


@pytest.mark.parametrize(
    "viewport",
    [
        {"width": 390, "height": 844},
        {"width": 768, "height": 1024},
        {"width": 1440, "height": 1000},
    ],
)
def test_visitor_understands_product_and_opens_demo(
    tmp_path: Path, viewport: dict[str, int]
) -> None:
    browser_errors: list[str] = []
    with running_app(tmp_path / "output", hosted_demo=True) as url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport=viewport)
        page.on(
            "console",
            lambda message: (
                browser_errors.append(message.text) if message.type == "error" else None
            ),
        )
        page.goto(url)

        page.get_by_role("heading", name="Decide where retirement still works.").wait_for()
        assert page.get_by_text("Gates eliminate").first.is_visible()
        assert page.get_by_role(
            "heading", name="From “maybe there” to a decision you can inspect."
        ).is_visible()
        assert page.evaluate(
            "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
        )
        assert page.locator(".process-list li").evaluate_all(
            """items => items.every(item => {
                const box = item.getBoundingClientRect();
                return box.left >= 0 && box.right <= document.documentElement.clientWidth;
            })"""
        )
        page.get_by_role("link", name="See the finished demo").first.click()
        page.get_by_role("heading", name="Williamsburg leads this field.").wait_for()
        assert page.get_by_text("03 / Blocked, not hidden").is_visible()
        page.get_by_role("link", name="How Lifescape works →").click()
        page.get_by_role("heading", name="Decide where retirement still works.").wait_for()
        assert page.url.rstrip("/") == url
        assert browser_errors == []
        browser.close()


def test_landing_keyboard_focus_and_disclosure_contrast(tmp_path: Path) -> None:
    with running_app(tmp_path / "output", hosted_demo=True) as url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(url)

        focused_links: list[str] = []
        for _ in range(12):
            page.keyboard.press("Tab")
            focused_links.append(
                page.evaluate(
                    """() => {
                        const active = document.activeElement;
                        return active instanceof HTMLAnchorElement
                            ? active.getAttribute("href") || ""
                            : "";
                    }"""
                )
            )
        assert "#method" in focused_links
        assert "/demo" in focused_links
        assert "https://github.com/buildproven/lifescape" in focused_links

        local_source = page.get_by_role("link", name="View the source on GitHub ↗")
        local_source.focus()
        focus_style = local_source.evaluate(
            "element => ({ outline: getComputedStyle(element).outlineColor, "
            "shadow: getComputedStyle(element).boxShadow })"
        )
        assert focus_style["outline"] == "rgb(19, 37, 29)"
        assert focus_style["shadow"] != "none"

        disclosure_color = page.locator(".demo-disclosure").evaluate(
            """element => {
                const match = getComputedStyle(element).color.match(/[\\d.]+/g);
                return match ? match.map(Number) : [];
            }"""
        )
        assert len(disclosure_color) == 4
        assert (
            contrast_ratio(
                tuple(int(channel) for channel in disclosure_color[:3]),
                (32, 61, 48),
                disclosure_color[3],
            )
            >= 4.5
        )
        browser.close()


@pytest.mark.parametrize("width", [320, 390, 760, 768, 900, 1440])
def test_finished_demo_reflows_and_prioritizes_the_answer(tmp_path: Path, width: int) -> None:
    viewport_height = 1000
    with running_app(tmp_path / "output", hosted_demo=True) as url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": width, "height": viewport_height})
        page.goto(f"{url}/demo")

        heading = page.get_by_role("heading", name="Williamsburg leads this field.")
        heading.wait_for()
        product_frame = page.get_by_text(
            "Lifescape is an evidence-backed retirement-town decision engine"
        )
        assert product_frame.is_visible()
        heading_box = heading.bounding_box()
        product_frame_box = product_frame.bounding_box()
        answer_box = page.locator(".decision-answer").bounding_box()
        context_box = page.locator(".decision-context").bounding_box()
        assert heading_box is not None
        assert product_frame_box is not None
        assert answer_box is not None
        assert context_box is not None
        assert heading_box["y"] < viewport_height
        assert product_frame_box["y"] < viewport_height
        if width <= 760:
            assert answer_box["y"] < context_box["y"]
        else:
            assert context_box["x"] < answer_box["x"]
        badge_display = page.locator(".decision-answer").evaluate(
            "element => getComputedStyle(element, '::after').display"
        )
        assert (badge_display == "none") == (width <= 1100)
        assert page.get_by_text("Completed synthetic outcome").is_visible()
        assert page.evaluate(
            "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
        )
        browser.close()


def test_finished_demo_small_text_meets_contrast_requirement(tmp_path: Path) -> None:
    with running_app(tmp_path / "output", hosted_demo=True) as url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(f"{url}/demo")

        for selector in [
            ".ranking li:not(.is-leader) .rank",
            ".ranking li:not(.is-leader) small",
            ".blocked-ledger b",
        ]:
            color = page.locator(selector).first.evaluate(
                """element => {
                    const match = getComputedStyle(element).color.match(/[\\d.]+/g);
                    return match ? match.map(Number) : [];
                }"""
            )
            assert len(color) >= 3
            foreground = tuple(int(channel) for channel in color[:3])
            assert contrast_ratio(foreground, (241, 238, 229)) >= 4.5
        browser.close()


@pytest.mark.parametrize("viewport", VIEWPORTS)
def test_discovery_journey_is_keyboard_labelled_and_announced(
    tmp_path: Path, viewport: dict[str, int]
) -> None:
    with running_app(tmp_path / "output") as url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport=viewport)
        page.goto(url)
        start_search(page, limits=True)

        unlabeled = page.evaluate(
            """() => [...document.querySelectorAll(
                '.stage.is-active input, .stage.is-active select, .stage.is-active button'
            )].filter(el => el.type !== 'hidden' && !el.hidden && el.offsetParent !== null)
              .filter(el => !(el.labels?.length || el.getAttribute('aria-label')
                  || el.textContent.trim()))
              .map(el => el.outerHTML.slice(0, 80))"""
        )
        assert unlabeled == []
        page.get_by_label("Median home value at least").focus()
        page.keyboard.type("100000")
        page.get_by_role("button", name="Find places").focus()
        page.keyboard.press("Enter")
        page.locator(".match-card").first.wait_for()
        assert page.evaluate("document.activeElement.id") == "step-title"
        assert page.locator("#match-summary").get_attribute("role") == "status"
        decision = page.locator(".decision-button[data-decision=keep]").first
        decision.focus()
        page.keyboard.press("Enter")
        assert (
            page.locator(".decision-button[data-decision=keep]").first.get_attribute("aria-pressed")
            == "true"
        )
        assert page.evaluate("document.activeElement.dataset.decision") == "keep"
        toggle = page.locator(".why-toggle").first
        toggle.focus()
        page.keyboard.press("Enter")
        assert toggle.get_attribute("aria-expanded") == "true"
        assert page.locator(".why-slot").first.is_visible()
        outline = toggle.evaluate("el => getComputedStyle(el).outlineStyle")
        assert outline != "none"
        browser.close()


def test_discovery_text_meets_contrast_requirement(tmp_path: Path) -> None:
    paper, white = (243, 240, 232), (252, 251, 247)
    with running_app(tmp_path / "output") as url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport=VIEWPORTS[1])
        page.goto(url)

        def assert_contrast(selector: str, background: tuple[int, int, int]) -> None:
            locator = page.locator(selector).first
            locator.wait_for(state="attached")
            color = locator.evaluate(
                """element => {
                    const match = getComputedStyle(element).color.match(/[\\d.]+/g);
                    return match ? match.map(Number) : [];
                }"""
            )
            foreground = tuple(int(channel) for channel in color[:3])
            assert contrast_ratio(foreground, background) >= 4.5, selector

        start_search(page)
        page.get_by_label("A town you already like").fill("Abanda")
        page.get_by_text("Examples need a population").first.wait_for()
        assert_contrast(".lookup-note", paper)
        assert_contrast("#exemplar-help", paper)
        page.get_by_role("button", name="Find places").click()
        page.locator(".match-card").first.wait_for()
        page.evaluate("document.querySelector('#match-list .movement').textContent = 'Moved'")
        for selector in (".card-label", ".trade-off", ".movement"):
            assert_contrast(f"#match-list {selector}", white)
        assert_contrast("#match-list .match-score", (23, 62, 47))
        browser.close()


def test_discovery_search_text_is_inert_untrusted_input(tmp_path: Path) -> None:
    payload = (
        '<img src=x onerror="window.__injected = true"><script>window.__injected = true</script>'
    )
    with running_app(tmp_path / "output") as url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport=VIEWPORTS[1])
        errors = watch_errors(page)
        page.goto(url)
        page.get_by_label("A town you already like").fill(payload)
        page.get_by_text("No U.S. town matches").wait_for()

        assert page.evaluate("window.__injected === undefined")
        assert page.locator("#exemplar-results img").count() == 0
        assert payload in page.locator("#exemplar-results").inner_text()
        assert errors == []
        browser.close()


# -- ease of use: a first-time visitor with no instructions -------------------------------


def test_first_run_try_example_reaches_matches_in_one_click(tmp_path: Path) -> None:
    with running_app(tmp_path / "output") as url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport=VIEWPORTS[0])
        errors = watch_errors(page)
        page.goto(url)
        page.get_by_role("button", name="Try it with Traverse City, MI").click()
        page.locator("#match-list .match-card").first.wait_for()

        assert page.locator("#match-list .match-card").count() == 10
        assert page.get_by_role("heading", name="Explore your matches").is_visible()
        assert errors == []
        browser.close()


@pytest.mark.parametrize("viewport", VIEWPORTS)
def test_first_run_style_alone_reaches_matches_in_two_clicks(
    tmp_path: Path, viewport: dict[str, int]
) -> None:
    with running_app(tmp_path / "output") as url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport=viewport)
        page.goto(url)
        # The first screen shows its main choices without scrolling.
        assert page.get_by_label("A town you already like").is_visible()
        for name in ("Affordable and quiet", "Lively and walkable"):
            box = page.get_by_role("button", name=name).bounding_box()
            assert box is not None and box["y"] + box["height"] <= viewport["height"]

        page.get_by_role("button", name="College town").click()
        assert page.get_by_role("button", name="College town").get_attribute("aria-pressed") == (
            "true"
        )
        page.get_by_text("Comparing 3 qualities.").wait_for()
        page.get_by_role("button", name="Find places").click()
        page.locator("#match-list .match-card").first.wait_for()

        names = page.locator("#match-list .match-card h3").all_inner_texts()
        assert len(names) == 10
        browser.close()


def test_style_can_be_switched_and_cleared(tmp_path: Path) -> None:
    with running_app(tmp_path / "output") as url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport=VIEWPORTS[1])
        page.goto(url)
        page.get_by_role("button", name="College town").click()
        page.get_by_role("button", name="Retiree-friendly").click()
        assert page.get_by_role("button", name="College town").get_attribute("aria-pressed") == (
            "false"
        )
        page.get_by_role("button", name="Retiree-friendly").click()
        page.get_by_text("Pick a town you like or a style above to begin.").wait_for()
        assert page.get_by_role("button", name="Find places").is_disabled()
        browser.close()


def test_style_fine_tune_is_collapsed_until_wanted(tmp_path: Path) -> None:
    with running_app(tmp_path / "output") as url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport=VIEWPORTS[0])
        page.goto(url)
        page.get_by_role("button", name="Lively and walkable").click()

        assert page.locator("#quality-list .quality-row").first.is_hidden()
        page.get_by_text("Fine-tune what matters").click()
        assert page.locator("#quality-list .quality-row").first.is_visible()
        browser.close()


def test_research_checklist_lists_finalists_and_critical_facts_to_confirm(
    tmp_path: Path,
) -> None:
    with running_app(tmp_path / "output") as url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport=VIEWPORTS[1])
        page.goto(url)
        start_search(page)
        find_places(page)
        keep(page, 2)
        page.get_by_role("button", name="Review shortlist").click()
        page.get_by_role("button", name="Verify finalists").click()
        with page.expect_download() as download:
            page.get_by_role("button", name="Download research checklist").click()
        path = download.value.path()
        text = Path(path).read_text(encoding="utf-8")

        assert download.value.suggested_filename == "lifescape-research-checklist.md"
        assert text.count("\n## ") == 2
        assert "- [ ] Emergency department drive time" in text
        assert "not verified evidence" in text
        assert "Lifescape never guesses a missing value" in text
        browser.close()
