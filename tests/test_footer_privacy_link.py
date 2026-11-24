"""BUI-2164: the landing and demo page footers link the privacy policy.

REQ-FOOT-1: the landing page footer links /privacy. REQ-FOOT-2: the demo page footer links
/privacy. Both pages and /privacy are served only by the hosted site, so the link always resolves.
"""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

from fastapi.testclient import TestClient

from lifescape.web import create_app


class _FooterLinks(HTMLParser):
    """Collect (href, text) for each link inside the page's <footer> elements."""

    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._footer_depth = 0
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "footer":
            self._footer_depth += 1
        elif tag == "a" and self._footer_depth:
            self._href = dict(attrs).get("href") or ""
            self._text = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "footer":
            self._footer_depth -= 1
        elif tag == "a" and self._href is not None:
            self.links.append((self._href, " ".join("".join(self._text).split())))
            self._href = None

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)


def _hosted_page(tmp_path: Path, path: str) -> tuple[list[tuple[str, str]], int]:
    with TestClient(
        create_app(tmp_path / "output", hosted_demo=True),
        base_url="https://lifescape.buildproven.ai",
    ) as client:
        page = client.get(path)
        assert page.status_code == 200
        parser = _FooterLinks()
        parser.feed(page.text)
        privacy_status = client.get("/privacy").status_code
    return parser.links, privacy_status


def _assert_privacy_link(links: list[tuple[str, str]], privacy_status: int) -> None:
    privacy = [text for href, text in links if href == "/privacy"]
    assert privacy == ["Privacy"], links
    assert privacy_status == 200


def test_req_foot_1_landing_footer_links_the_privacy_policy(tmp_path: Path) -> None:
    links, privacy_status = _hosted_page(tmp_path, "/")
    _assert_privacy_link(links, privacy_status)


def test_req_foot_1_landing_footer_keeps_the_github_link(tmp_path: Path) -> None:
    links, _ = _hosted_page(tmp_path, "/")
    assert ("https://github.com/buildproven/lifescape", "GitHub ↗") in links


def test_req_foot_2_demo_footer_links_the_privacy_policy(tmp_path: Path) -> None:
    links, privacy_status = _hosted_page(tmp_path, "/demo")
    _assert_privacy_link(links, privacy_status)


def test_req_foot_2_demo_footer_keeps_the_how_it_works_link(tmp_path: Path) -> None:
    links, _ = _hosted_page(tmp_path, "/demo")
    assert ("/", "How it works →") in links
