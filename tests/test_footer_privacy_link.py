"""BUI-2164: the landing and demo page footers link the privacy policy."""

from __future__ import annotations

from collections.abc import Iterator
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lifescape.web import create_app


class _FooterLinks(HTMLParser):
    """Collect the href and text of every link inside each <footer>."""

    def __init__(self) -> None:
        super().__init__()
        self.footers: list[list[tuple[str, str]]] = []
        self._depth = 0
        self._href: str | None = None
        self._text = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "footer":
            if self._depth == 0:
                self.footers.append([])
            self._depth += 1
        elif tag == "a" and self._depth:
            self._href = dict(attrs).get("href") or ""
            self._text = ""

    def handle_endtag(self, tag: str) -> None:
        if tag == "footer" and self._depth:
            self._depth -= 1
        elif tag == "a" and self._href is not None:
            self.footers[-1].append((self._href, " ".join(self._text.split())))
            self._href = None

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text += data


@pytest.fixture(scope="module")
def hosted(tmp_path_factory: pytest.TempPathFactory) -> Iterator[TestClient]:
    output_dir = tmp_path_factory.mktemp("footer") / "output"
    with TestClient(
        create_app(output_dir, hosted_demo=True),
        base_url="https://lifescape.buildproven.ai",
    ) as client:
        yield client


def _site_footer_links(client: TestClient, path: str) -> list[tuple[str, str]]:
    page = client.get(path)
    assert page.status_code == 200
    parser = _FooterLinks()
    parser.feed(page.text)
    assert parser.footers, f"{path} has no footer"
    return parser.footers[-1]


@pytest.mark.parametrize("path", ["/", "/demo"])
def test_bui_2164_req_1_footer_links_the_privacy_policy(hosted: TestClient, path: str) -> None:
    links = _site_footer_links(hosted, path)
    assert ("/privacy", "Privacy policy") in links


@pytest.mark.parametrize("path", ["/", "/demo"])
def test_bui_2164_req_1_footer_privacy_link_resolves(hosted: TestClient, path: str) -> None:
    hrefs = [href for href, _ in _site_footer_links(hosted, path)]
    privacy = [href for href in hrefs if "privacy" in href]
    assert privacy, f"{path} footer has no privacy link"
    for href in privacy:
        response = hosted.get(href)
        assert response.status_code == 200
        assert "<title>Privacy policy — Lifescape</title>" in response.text


@pytest.mark.parametrize("path", ["/", "/demo"])
def test_bui_2164_req_1_footer_keeps_its_existing_links(hosted: TestClient, path: str) -> None:
    hrefs = [href for href, _ in _site_footer_links(hosted, path)]
    assert hrefs[0] == "/"
    if path == "/":
        assert "https://github.com/buildproven/lifescape" in hrefs


@pytest.mark.parametrize(
    ("template", "footer_class"),
    [("landing.html", "site-footer"), ("demo.html", "demo-footer")],
)
def test_bui_2164_req_1_footer_keeps_three_grid_children(template: str, footer_class: str) -> None:
    """The footer CSS is a three-column grid; a fourth direct child would wrap to a new row."""
    source = (Path(__file__).parents[1] / "src" / "lifescape" / "templates" / template).read_text(
        encoding="utf-8"
    )
    start = source.index(f'<footer class="{footer_class}">')
    footer = source[start : source.index("</footer>", start)]

    class _Children(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.depth = 0
            self.children: list[str] = []

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            if self.depth == 1:
                self.children.append(tag)
            self.depth += 1

        def handle_endtag(self, tag: str) -> None:
            self.depth -= 1

    parser = _Children()
    parser.feed(footer + "</footer>")
    assert len(parser.children) == 3, parser.children
