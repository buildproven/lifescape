"""The primary action must be disabled in the served HTML, before any script runs.

Rendering it enabled and disabling it after load shows an active "Find places" button on a slow
connection and races the journey test in CI (PRD FR1: Find places stays disabled until the user
picks a town or style).
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from lifescape.web import create_app


def test_find_places_button_is_disabled_in_the_served_html(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "output"), base_url="http://127.0.0.1") as client:
        html = client.get("/").text

    button = re.search(r"<button[^>]*id=\"next-button\"[^>]*>", html)
    assert button is not None
    assert re.search(r"\bdisabled\b", button.group(0))
