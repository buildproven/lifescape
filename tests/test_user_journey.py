from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import uvicorn
from playwright.sync_api import Page, sync_playwright

from retirement_engine.web import create_app


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


def test_user_completes_guided_comparison(tmp_path: Path) -> None:
    browser_errors: list[str] = []
    with running_app(tmp_path / "output") as url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page: Page = browser.new_page(viewport={"width": 390, "height": 844})
        page.on(
            "console",
            lambda message: (
                browser_errors.append(message.text) if message.type == "error" else None
            ),
        )
        page.goto(url)

        page.get_by_role("heading", name="Shape the decision").wait_for()
        page.get_by_role("button", name="Choose towns →").click()
        page.get_by_role("heading", name="Choose a meaningful field").wait_for()
        page.get_by_role("button", name="Review evidence →").click()
        page.get_by_text("99%").wait_for()
        page.get_by_role("button", name="Run comparison →").click()

        page.get_by_role("heading", name="Williamsburg leads this field.").wait_for()
        assert page.get_by_role("heading", name="Blocked, not hidden").is_visible()
        assert page.get_by_role("link", name="Markdown report").is_visible()
        assert page.get_by_role("button", name="Adjust comparison ↺").is_visible()
        assert browser_errors == []
        browser.close()
