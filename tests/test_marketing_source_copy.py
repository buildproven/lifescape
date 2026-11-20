"""The demo and landing pages name every discovery source (BUI-1549; PRD G3).

Manual read on main (6c0604d): the demo "1 · Discover" step said the local app suggests
"candidate towns from public Census data", and the landing "local" section said the installable
app "finds places from public Census data", while discovery also ranks on NOAA climate normals,
place character (USGS/Natural Earth) and the nearest emergency hospital (CMS). The in-app notice
and research checklist already name every source (REQ-SRC-1, REQ-SRC-2).
"""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

import pytest

TEMPLATES = Path(__file__).resolve().parents[1] / "src" / "lifescape" / "templates"
SOURCE_GROUPS = ("Census", "NOAA climate", "place character", "USGS", "Natural Earth", "CMS")
CENSUS_ONLY = "from public Census data"


class _PageText(HTMLParser):
    """Collect the text of each <li> and <p>, so a sentence is checked as one unit."""

    def __init__(self) -> None:
        super().__init__()
        self.blocks: list[str] = []
        self.parts: list[str] | None = None
        self.depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.parts is not None:
            self.depth += 1
        elif tag in ("li", "p"):
            self.parts = []
            self.depth = 1

    def handle_endtag(self, tag: str) -> None:
        if self.parts is None:
            return
        self.depth -= 1
        if self.depth == 0:
            self.blocks.append(" ".join(" ".join(self.parts).split()))
            self.parts = None

    def handle_data(self, data: str) -> None:
        if self.parts is not None:
            self.parts.append(data)


def blocks(template: str) -> list[str]:
    parser = _PageText()
    parser.feed((TEMPLATES / template).read_text(encoding="utf-8"))
    return parser.blocks


def demo_discover_step() -> str:
    found = [text for text in blocks("demo.html") if text.startswith("1 · Discover")]
    assert len(found) == 1, "demo.html must have exactly one '1 · Discover' step"
    return found[0]


def landing_install_copy() -> str:
    found = [text for text in blocks("landing.html") if "The installable app finds places" in text]
    assert len(found) == 1, "landing.html must have one paragraph describing the installable app"
    return found[0]


@pytest.mark.parametrize("source", SOURCE_GROUPS)
def test_req_src_3_demo_discover_step_names_each_source_group(source: str) -> None:
    assert source in demo_discover_step()


def test_req_src_3_demo_discover_step_does_not_say_census_alone() -> None:
    assert CENSUS_ONLY not in demo_discover_step()


@pytest.mark.parametrize("source", SOURCE_GROUPS)
def test_req_src_4_landing_install_copy_names_each_source_group(source: str) -> None:
    assert source in landing_install_copy()


@pytest.mark.parametrize("template", ["demo.html", "landing.html"])
def test_req_src_3_4_no_page_says_census_alone(template: str) -> None:
    text = (TEMPLATES / template).read_text(encoding="utf-8")
    assert CENSUS_ONLY not in " ".join(text.split())
