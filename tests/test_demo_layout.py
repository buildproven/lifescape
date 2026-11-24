"""BUI-2209: the demo stamp reads on two lines and every demo section keeps the page gutter."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright
from test_user_journey import running_app

ROOT = Path(__file__).resolve().parents[1]
DEMO_CSS = ROOT / "src" / "lifescape" / "static" / "demo.css"
DEMO_HTML = ROOT / "src" / "lifescape" / "templates" / "demo.html"

# Sections that span the full window on purpose and set their own inner gutter.
FULL_BLEED_SECTIONS = {"demo-cta"}
GUTTER_DECLARATIONS = {"width": "var(--page)", "margin": "0 auto"}


def top_level_rules(css: str) -> list[tuple[list[str], dict[str, str]]]:
    """Return (selectors, declarations) for each rule outside @media blocks."""
    without_comments = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    rules: list[tuple[list[str], dict[str, str]]] = []
    depth = 0
    start = 0
    for index, char in enumerate(without_comments):
        if char == "{":
            if depth == 0:
                prelude = without_comments[start:index].strip()
                body_start = index + 1
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                body = without_comments[body_start:index]
                start = index + 1
                if prelude.startswith("@") or "{" in body:
                    continue
                selectors = [selector.strip() for selector in prelude.split(",")]
                declarations: dict[str, str] = {}
                for declaration in body.split(";"):
                    name, separator, value = declaration.partition(":")
                    if separator:
                        declarations[name.strip()] = " ".join(value.split())
                rules.append((selectors, declarations))
    return rules


def declarations_for(css: str, selector: str) -> dict[str, str]:
    merged: dict[str, str] = {}
    for selectors, declarations in top_level_rules(css):
        if selector in selectors:
            merged.update(declarations)
    return merged


class TopLevelSections(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_main = False
        self.depth = 0
        self.classes: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "main":
            self.in_main = True
            self.depth = 0
            return
        if not self.in_main:
            return
        if tag == "section" and self.depth == 0:
            self.classes.append(dict(attrs).get("class") or "")
        if tag not in {"br", "img", "source", "input", "meta", "link", "hr"}:
            self.depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "main":
            self.in_main = False
        elif self.in_main and tag not in {"br", "img", "source", "input", "meta", "link", "hr"}:
            self.depth -= 1


def test_bui_2209_stamp_content_uses_css_line_break_escape() -> None:
    css = DEMO_CSS.read_text(encoding="utf-8")
    for match in re.finditer(r"content\s*:\s*([^;]*);", css):
        assert "\\\\A" not in match.group(1), f"literal backslash in {match.group(0)}"
    stamp = declarations_for(css, ".decision-answer::after")
    assert stamp["content"] == '"LEAD\\A CANDIDATE"'
    assert stamp["white-space"] == "pre"


def test_bui_2209_path_section_keeps_ranking_gutter() -> None:
    css = DEMO_CSS.read_text(encoding="utf-8")
    ranking = declarations_for(css, ".ranking")
    path = declarations_for(css, ".path")
    for name, value in GUTTER_DECLARATIONS.items():
        assert ranking[name] == value
        assert path[name] == ranking[name], f".path {name} differs from .ranking"


def test_bui_2209_every_demo_section_keeps_page_gutter() -> None:
    css = DEMO_CSS.read_text(encoding="utf-8")
    parser = TopLevelSections()
    parser.feed(DEMO_HTML.read_text(encoding="utf-8"))
    assert parser.classes, "demo.html has no top-level sections"
    for class_attr in parser.classes:
        names = class_attr.split()
        assert names, "top-level demo section has no class"
        if FULL_BLEED_SECTIONS.intersection(names):
            continue
        merged: dict[str, str] = {}
        for name in names:
            merged.update(declarations_for(css, f".{name}"))
        for declaration, value in GUTTER_DECLARATIONS.items():
            assert merged.get(declaration) == value, f".{names[0]} lacks {declaration}: {value}"


@pytest.mark.parametrize("width", [1101, 1280, 1606, 1920])
def test_bui_2209_rendered_stamp_and_path_gutter(tmp_path: Path, width: int) -> None:
    with (
        running_app(tmp_path / "output", hosted_demo=True) as url,
        sync_playwright() as playwright,
    ):
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": width, "height": 1000})
        page.goto(f"{url}/demo")
        stamp = page.locator(".decision-answer").evaluate(
            """element => {
                const style = getComputedStyle(element, '::after');
                const box = element.getBoundingClientRect();
                const matrix = new DOMMatrixReadOnly(style.transform);
                const w = parseFloat(style.width), h = parseFloat(style.height);
                const left = box.right - parseFloat(style.right) - w;
                const top = box.top + parseFloat(style.top);
                const cx = left + w / 2, cy = top + h / 2;
                const corners = [[-w / 2, -h / 2], [w / 2, -h / 2], [w / 2, h / 2], [-w / 2, h / 2]]
                    .map(([x, y]) => matrix.transformPoint(new DOMPoint(x, y)));
                const xs = corners.map(p => cx + p.x), ys = corners.map(p => cy + p.y);
                return {display: style.display, content: style.content,
                        left: Math.min(...xs), right: Math.max(...xs),
                        top: Math.min(...ys), bottom: Math.max(...ys)};
            }"""
        )
        assert stamp["display"] != "none"
        assert stamp["content"] == '"LEAD\\a CANDIDATE"'
        text_boxes = page.evaluate(
            """() => [...document.querySelectorAll('.decision-answer > *')].flatMap(element => {
                const range = document.createRange();
                range.selectNodeContents(element);
                return [...range.getClientRects()].map(r => ({
                    left: r.left, right: r.right, top: r.top, bottom: r.bottom,
                    tag: element.tagName}));
            })"""
        )
        for box in text_boxes:
            overlaps = (
                box["left"] < stamp["right"]
                and stamp["left"] < box["right"]
                and box["top"] < stamp["bottom"]
                and stamp["top"] < box["bottom"]
            )
            assert not overlaps, f"stamp overlaps {box['tag']} text at {width}px: {box}"
        ranking = page.locator(".ranking").bounding_box()
        path = page.locator(".path").bounding_box()
        assert ranking is not None
        assert path is not None
        assert path["x"] == pytest.approx(ranking["x"], abs=1)
        assert path["width"] == pytest.approx(ranking["width"], abs=1)
        browser.close()
