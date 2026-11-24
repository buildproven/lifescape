"""BUI-2199: the landing page shows how to use Lifescape in Claude."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lifescape.web import create_app

ROOT = Path(__file__).parents[1]
EXAMPLE_ASKS = (
    "find towns like Traverse City with mild winters",
    "compare Petoskey and Bath",
    "show them on a map",
)


class _Section(HTMLParser):
    """Collect the text and links of the section whose heading has the given id."""

    def __init__(self, heading_id: str) -> None:
        super().__init__()
        self.heading_id = heading_id
        self.sections: list[dict[str, list[str]]] = []
        self.headings: list[str] = []
        self._depth = 0
        self._in_heading = False
        self._current: dict[str, list[str]] | None = None
        self._order: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "section":
            self._order.append(values.get("aria-labelledby") or values.get("class") or "")
            if values.get("aria-labelledby") == self.heading_id:
                self._current = {"text": [], "links": []}
                self.sections.append(self._current)
                self._depth = 0
            if self._current is not None:
                self._depth += 1
        if self._current is None:
            return
        if tag == "a" and values.get("href"):
            self._current["links"].append(values["href"] or "")
        if values.get("id") == self.heading_id:
            self._in_heading = True
            self.headings.append("")

    def handle_endtag(self, tag: str) -> None:
        if self._current is None:
            return
        if tag in {"h1", "h2", "h3"}:
            self._in_heading = False
        if tag == "section":
            self._depth -= 1
            if self._depth == 0:
                self._current = None

    def handle_data(self, data: str) -> None:
        if self._current is None:
            return
        self._current["text"].append(data)
        if self._in_heading:
            self.headings[-1] += data

    @property
    def section_order(self) -> list[str]:
        return self._order


def _landing(output_dir: Path) -> str:
    with TestClient(
        create_app(output_dir, hosted_demo=True),
        base_url="https://lifescape.buildproven.ai",
    ) as client:
        page = client.get("/")
    assert page.status_code == 200
    return page.text


def _readme_install_links() -> list[str]:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    match = re.search(r"^### Install in Claude Desktop\n(.*?)^#", readme, re.M | re.S)
    assert match, "README has no 'Install in Claude Desktop' section"
    links = re.findall(r"\]\((https://[^)\s]+)\)", match.group(1))
    assert links, "README install section names no install link"
    return links


@pytest.fixture(scope="module")
def claude_section(tmp_path_factory: pytest.TempPathFactory) -> _Section:
    parser = _Section("claude-title")
    parser.feed(_landing(tmp_path_factory.mktemp("landing") / "output"))
    assert len(parser.sections) == 1, "landing page needs exactly one 'Use it in Claude' section"
    return parser


def _text(section: _Section) -> str:
    return " ".join("".join(section.sections[0]["text"]).split())


def test_bui_2199_req_1_section_has_heading_right_after_the_hero(claude_section: _Section) -> None:
    assert [heading.strip() for heading in claude_section.headings] == ["Use it in Claude"]
    order = claude_section.section_order
    assert order[0] == "hero-title"
    assert order[1] == "claude-title"


@pytest.mark.parametrize("ask", EXAMPLE_ASKS)
def test_bui_2199_req_1_section_lists_each_example_ask(claude_section: _Section, ask: str) -> None:
    assert f"“{ask}”" in _text(claude_section)


def test_bui_2199_req_1_section_says_to_add_the_plugin_first(claude_section: _Section) -> None:
    text = _text(claude_section)
    assert "Add the Lifescape plugin" in text
    assert text.index("Add the Lifescape plugin") < text.index(EXAMPLE_ASKS[0])


def test_bui_2199_req_2_section_links_the_readme_install_source(claude_section: _Section) -> None:
    section_links = claude_section.sections[0]["links"]
    for link in _readme_install_links():
        assert link in section_links
    external = [link for link in section_links if link.startswith("http")]
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for link in external:
        assert link.split("#")[0] in readme, f"{link} is not named in the README"
