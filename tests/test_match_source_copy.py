"""The match list and research checklist name every discovery source (BUI-1488; PRD G3).

Manual read on main (f21093c): the `#catalog-note` notice said "These matches come from public
Census data" and the research checklist intro said "Matches come from public Census data", while
discovery also ranks on NOAA climate normals, place character (USGS/Natural Earth) and the
nearest emergency hospital (CMS).
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "src" / "lifescape"
SOURCE_GROUPS = ("Census", "NOAA climate", "place character", "USGS", "Natural Earth", "CMS")
CENSUS_ONLY = "from public Census data"


class _ElementText(HTMLParser):
    """Collect the text inside the element with one id."""

    def __init__(self, element_id: str) -> None:
        super().__init__()
        self.element_id = element_id
        self.depth = 0
        self.found = False
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.depth:
            self.depth += 1
        elif dict(attrs).get("id") == self.element_id:
            self.depth = 1
            self.found = True

    def handle_endtag(self, tag: str) -> None:
        if self.depth:
            self.depth -= 1

    def handle_data(self, data: str) -> None:
        if self.depth:
            self.parts.append(data)

    def text(self) -> str:
        return " ".join(" ".join(self.parts).split())


def catalog_note_text() -> str:
    parser = _ElementText("catalog-note")
    parser.feed((ROOT / "templates" / "app.html").read_text(encoding="utf-8"))
    assert parser.found, "#catalog-note is missing from app.html"
    return parser.text()


def checklist_intro_text() -> str:
    script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    body = re.search(r"function researchChecklist\(\) \{(.*?)\n\}", script, re.S)
    assert body, "researchChecklist() is missing from app.js"
    intro = re.search(r"lines\.push\((.*?)\);", body.group(1), re.S)
    assert intro, "researchChecklist() has no intro lines.push call"
    strings = re.findall(r'"((?:[^"\\]|\\.)*)"', intro.group(1))
    return " ".join(" ".join(strings).split())


@pytest.mark.parametrize("source", SOURCE_GROUPS)
def test_req_src_1_catalog_note_names_each_source_group(source: str) -> None:
    assert source in catalog_note_text()


def test_req_src_1_catalog_note_does_not_say_census_alone() -> None:
    assert CENSUS_ONLY not in catalog_note_text()


@pytest.mark.parametrize("source", SOURCE_GROUPS)
def test_req_src_2_checklist_intro_names_each_source_group(source: str) -> None:
    assert source in checklist_intro_text()


def test_req_src_2_checklist_intro_does_not_say_census_alone() -> None:
    text = checklist_intro_text()
    assert CENSUS_ONLY not in text
    assert "Lifescape never guesses a missing value." in text
