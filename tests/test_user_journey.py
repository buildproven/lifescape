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

from retirement_engine.web import create_app


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
def running_app(output_dir: Path, *, hosted_demo: bool = False) -> Iterator[str]:
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


@pytest.mark.parametrize(
    "viewport", [{"width": 390, "height": 844}, {"width": 1440, "height": 1000}]
)
def test_user_completes_guided_comparison(tmp_path: Path, viewport: dict[str, int]) -> None:
    browser_errors: list[str] = []
    with running_app(tmp_path / "output") as url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page: Page = browser.new_page(viewport=viewport)
        page.on(
            "console",
            lambda message: (
                browser_errors.append(message.text) if message.type == "error" else None
            ),
        )
        page.goto(f"{url}/demo")

        page.get_by_role("heading", name="Shape the decision").wait_for()
        page.get_by_role("button", name="Choose towns →").click()
        page.get_by_role("heading", name="Choose a meaningful field").wait_for()
        page.get_by_role("button", name="Review evidence →").click()
        page.get_by_text("99%").wait_for()
        page.get_by_role("button", name="Run comparison →").click()

        page.get_by_role("heading", name="Williamsburg leads this field.").wait_for()
        assert page.get_by_role("heading", name="Blocked, not hidden").is_visible()
        assert page.get_by_role("link", name="Markdown report").is_visible()
        assert page.get_by_role("link", name="SQLite provenance").is_visible()
        assert page.get_by_role("button", name="Adjust comparison ↺").is_visible()
        assert browser_errors == []
        browser.close()


def test_user_keeps_mixed_evidence_warning_after_scoring(tmp_path: Path) -> None:
    evidence = Path("data/benchmarks/evidence.csv").read_text(encoding="utf-8")
    mixed_evidence = evidence.replace(",true,", ",false,", 1)
    browser_errors: list[str] = []
    with running_app(tmp_path / "output") as url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.on(
            "console",
            lambda message: (
                browser_errors.append(message.text) if message.type == "error" else None
            ),
        )
        page.goto(f"{url}/demo")
        page.get_by_role("heading", name="Shape the decision").wait_for()
        page.locator("#evidence-file").set_input_files(
            {
                "name": "oversized.csv",
                "mimeType": "text/csv",
                "buffer": b"x" * 5_000_001,
            }
        )
        page.get_by_text("Evidence CSV exceeds the 5 MB local-app limit.").wait_for()
        page.locator("#evidence-file").set_input_files(
            {
                "name": "mixed.csv",
                "mimeType": "text/csv",
                "buffer": mixed_evidence.encode(),
            }
        )
        page.wait_for_function(
            "() => document.querySelector('#dataset-meta').textContent.includes('mixed evidence')"
        )
        page.get_by_role("button", name="Choose towns →").click()
        page.get_by_role("button", name="Review evidence →").click()
        page.get_by_role("button", name="Run comparison →").click()
        page.get_by_role("heading", name="Williamsburg leads this field.").wait_for()
        warning = (
            "This run contains synthetic values. Treat its results as test output, "
            "not purchase research."
        )
        notice = page.locator("#synthetic-notice")
        assert notice.is_visible()
        assert warning in notice.inner_text()
        assert browser_errors == []
        browser.close()


def test_hosted_user_completes_synthetic_demo_without_private_controls(
    tmp_path: Path,
) -> None:
    browser_errors: list[str] = []
    output = tmp_path / "output"
    with running_app(output, hosted_demo=True) as url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on(
            "console",
            lambda message: (
                browser_errors.append(message.text) if message.type == "error" else None
            ),
        )
        page.goto(f"{url}/demo")
        page.get_by_role("heading", name="Shape the decision").wait_for()
        assert page.get_by_role("button", name="Import CSV").is_hidden()
        assert page.get_by_text(
            "CSV uploads are disabled. Your selected constraints are processed "
            "temporarily; no durable record is promised."
        ).is_visible()
        page.get_by_role("button", name="Choose towns →").click()
        page.get_by_role("button", name="Review evidence →").click()
        page.get_by_role("button", name="Run comparison →").click()
        page.get_by_role("heading", name="Williamsburg leads this field.").wait_for()
        assert page.get_by_text("Hosted demonstration").is_visible()
        assert page.get_by_text(
            "Install Lifescape locally to import private evidence and save provenance."
        ).is_visible()
        assert page.get_by_text(
            "This synthetic run is temporary. Adjust the inputs to explore another field."
        ).is_visible()
        assert page.get_by_role("link", name="Markdown report").count() == 0
        assert browser_errors == []
        browser.close()

    assert list((output / "runs").iterdir()) == []


def test_hosted_disclosure_survives_without_javascript(tmp_path: Path) -> None:
    with running_app(tmp_path / "output", hosted_demo=True) as url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(java_script_enabled=False, viewport={"width": 1440, "height": 1000})
        page.goto(f"{url}/demo")

        assert page.get_by_text("Public demo").is_visible()
        assert page.get_by_text(
            "CSV uploads are disabled. Your selected constraints are processed "
            "temporarily; no durable record is promised."
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
        assert page.get_by_text("Synthetic example").is_visible()
        assert page.get_by_text(
            "The hosted experience accepts only bundled synthetic evidence and keeps no durable "
            "run record."
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
        page.get_by_role("link", name="Try the synthetic demo").click()
        page.get_by_role("heading", name="Shape the decision").wait_for()
        assert page.url.endswith("/demo")
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
        assert "https://github.com/buildproven/lifescape-engine" in focused_links

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
