"""Demo page stamp and section gutter (BUI-2209; PRD FR16).

Seen on the hosted demo (Chrome, 1606 px, 2026-11-24): the round stamp read the literal text
``LEAD\\A CANDIDATE`` over the headline, and the "How this field was found." section started at
x=0 with no page gutter.

- REQ-DEMO-STAMP-1: the stamp reads "LEAD" and "CANDIDATE" on two lines; at 1024 px and wider it
  does not overlap the headline, and below 1024 px it sits above the headline.
- REQ-DEMO-GUTTER-1: every top-level section of the demo page, "How this field was found."
  included, keeps the same left and right gutter as the ranking section.

The browser tests start a Playwright page; run this file on its own.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

import pytest
from playwright.sync_api import Page, sync_playwright
from test_user_journey import running_app

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "lifescape"
DEMO_CSS = PACKAGE / "static" / "demo.css"
DEMO_HTML = PACKAGE / "templates" / "demo.html"
STAMP = ".decision-answer::after"
PAGE_WIDTH = "var(--page)"


def top_level_rules(css: str) -> list[tuple[list[str], dict[str, str]]]:
    """Return (selectors, declarations) for each rule outside an at-rule, in source order."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    rules: list[tuple[list[str], dict[str, str]]] = []
    depth = 0
    start = 0
    prelude = ""
    for index, char in enumerate(css):
        if char == "{":
            if depth == 0:
                prelude = css[start:index].strip()
                start = index + 1
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                if not prelude.startswith("@"):
                    declarations: dict[str, str] = {}
                    for declaration in css[start:index].split(";"):
                        name, colon, value = declaration.partition(":")
                        if colon:
                            declarations[name.strip()] = " ".join(value.split())
                    selectors = [" ".join(part.split()) for part in prelude.split(",")]
                    rules.append((selectors, declarations))
                start = index + 1
    return rules


def declarations_for(css: str, selector: str) -> dict[str, str]:
    """Merge every top-level rule that names the selector, later rules winning."""
    merged: dict[str, str] = {}
    for selectors, declarations in top_level_rules(css):
        if selector in selectors:
            merged.update(declarations)
    return merged


def horizontal_box(declarations: dict[str, str], prop: str) -> tuple[str, str]:
    """Return the (left, right) values of a margin or padding shorthand and its longhands."""
    # Shorthand order is top, right, bottom, left; split on spaces outside parentheses.
    parts: list[str] = [""]
    depth = 0
    for char in declarations.get(prop, "0"):
        depth += {"(": 1, ")": -1}.get(char, 0)
        if char == " " and depth == 0:
            parts.append("")
        else:
            parts[-1] += char
    if len(parts) == 1:
        left = right = parts[0]
    elif len(parts) in (2, 3):
        left = right = parts[1]
    else:
        right, left = parts[1], parts[3]
    left = declarations.get(f"{prop}-left", left)
    right = declarations.get(f"{prop}-right", right)
    return left, right


def uses_page_gutter(declarations: dict[str, str]) -> bool:
    """A section keeps the gutter by taking the shared page width, centred with auto margins."""
    return declarations.get("width") == PAGE_WIDTH and horizontal_box(declarations, "margin") == (
        "auto",
        "auto",
    )


def uses_band_gutter(declarations: dict[str, str]) -> bool:
    """A full-bleed coloured band keeps the gutter by padding to the page's 1450 px width."""
    left, right = horizontal_box(declarations, "padding")
    return "background" in declarations and left == right and "1450px" in left


def section_classes(html: str) -> list[str]:
    main = html.split("<main", 1)[1].split("</main>", 1)[0]
    classes: list[str] = []
    depth = 0
    for match in re.finditer(r"<(/?)section\b([^>]*)>", main):
        closing, attributes = match.groups()
        if closing:
            depth -= 1
            continue
        if depth == 0:
            found = re.search(r'class="([^"]+)"', attributes)
            assert found, f"top-level section without a class: {match.group(0)}"
            classes.append(found.group(1).split()[0])
        depth += 1
    return classes


def test_req_demo_stamp_1_stamp_content_uses_a_line_break_not_a_literal_backslash() -> None:
    css = DEMO_CSS.read_text(encoding="utf-8")
    contents = re.findall(r"content:\s*([^;]+);", css)
    assert contents
    for value in contents:
        assert "\\\\A" not in value.upper(), f"literal backslash in content: {value}"
    stamp = declarations_for(css, STAMP)
    assert stamp["content"] == '"LEAD\\A CANDIDATE"'
    assert stamp["white-space"] == "pre"


def test_req_demo_gutter_1_path_section_uses_the_ranking_gutter() -> None:
    css = DEMO_CSS.read_text(encoding="utf-8")
    path = declarations_for(css, ".path")
    ranking = declarations_for(css, ".ranking")
    assert uses_page_gutter(ranking)
    assert path.get("width") == ranking["width"]
    assert horizontal_box(path, "margin") == horizontal_box(ranking, "margin")
    assert horizontal_box(path, "padding") == horizontal_box(ranking, "padding")


def test_req_demo_gutter_1_every_top_level_section_keeps_the_page_gutter() -> None:
    css = DEMO_CSS.read_text(encoding="utf-8")
    classes = section_classes(DEMO_HTML.read_text(encoding="utf-8"))
    assert {"decision-hero", "path", "ranking", "demo-cta"} <= set(classes)
    for name in classes:
        declarations = declarations_for(css, f".{name}")
        assert uses_page_gutter(declarations) or uses_band_gutter(declarations), (
            f".{name} does not keep the page gutter"
        )


def test_horizontal_box_reads_shorthand_and_longhands() -> None:
    assert horizontal_box({}, "padding") == ("0", "0")
    assert horizontal_box({"padding": "90px 0 20px"}, "padding") == ("0", "0")
    assert horizontal_box({"padding": "1px 2px 3px 4px"}, "padding") == ("4px", "2px")
    assert (
        horizontal_box({"padding": "115px max(36px, calc((100vw - 1450px) / 2))"}, "padding")
        == ("max(36px, calc((100vw - 1450px) / 2))",) * 2
    )
    assert horizontal_box({"margin": "0 auto", "margin-left": "3px"}, "margin") == ("3px", "auto")


STAMP_BOX = """element => {
    const style = getComputedStyle(element, '::after');
    const answer = element.getBoundingClientRect();
    const answerStyle = getComputedStyle(element);
    const width = parseFloat(style.width);
    const height = parseFloat(style.height);
    let top;
    let right;
    if (style.position === 'absolute') {
        top = answer.top + parseFloat(style.top);
        right = answer.right - parseFloat(style.right);
    } else {
        const first = element.firstElementChild.getBoundingClientRect();
        top = first.top - parseFloat(style.marginBottom) - height;
        right = answer.right - parseFloat(answerStyle.paddingRight)
            - parseFloat(style.marginRight);
    }
    return {
        display: style.display,
        content: style.content,
        whiteSpace: style.whiteSpace,
        transform: style.transform,
        x: right - width, y: top, width, height,
    };
}"""


TEXT_LINES = """element => {
    const range = document.createRange();
    range.selectNodeContents(element);
    return Array.from(range.getClientRects()).map(r => [r.left, r.top, r.right, r.bottom]);
}"""


def rotated_bounds(stamp: dict[str, float | str]) -> tuple[float, float, float, float]:
    """Return (left, top, right, bottom) of the stamp after its rotation about its centre."""
    width = float(stamp["width"])
    height = float(stamp["height"])
    match = re.match(r"matrix\(([-\d.e]+), ([-\d.e]+)", str(stamp["transform"]))
    cos, sin = (float(match.group(1)), float(match.group(2))) if match else (1.0, 0.0)
    span_x = abs(cos) * width + abs(sin) * height
    span_y = abs(sin) * width + abs(cos) * height
    centre_x = float(stamp["x"]) + width / 2
    centre_y = float(stamp["y"]) + height / 2
    assert math.isclose(math.hypot(cos, sin), 1, abs_tol=1e-3)
    return (
        centre_x - span_x / 2,
        centre_y - span_y / 2,
        centre_x + span_x / 2,
        centre_y + span_y / 2,
    )


def headline_box(page: Page) -> tuple[float, float, float, float]:
    box = page.get_by_role("heading", name="Williamsburg leads this field.").bounding_box()
    assert box is not None
    return box["x"], box["y"], box["x"] + box["width"], box["y"] + box["height"]


def overlaps(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


@pytest.mark.parametrize("width", [1024, 1100, 1440, 1606, 1920])
def test_req_demo_stamp_1_stamp_reads_two_lines_clear_of_the_headline_when_wide(
    tmp_path: Path, width: int
) -> None:
    with running_app(tmp_path / "output", hosted_demo=True) as url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": width, "height": 1000})
        page.goto(f"{url}/demo")
        stamp = page.locator(".decision-answer").evaluate(STAMP_BOX)
        assert stamp["display"] != "none"
        assert stamp["content"] == '"LEAD\\a CANDIDATE"'
        assert stamp["whiteSpace"] == "pre"
        bounds = rotated_bounds(stamp)
        assert not overlaps(bounds, headline_box(page))
        for selector in (".decision-answer .eyebrow", ".decision-answer .product-frame"):
            for line in page.locator(selector).evaluate(TEXT_LINES):
                assert not overlaps(bounds, tuple(line)), selector
        browser.close()


@pytest.mark.parametrize("width", [320, 390, 760, 900, 1023])
def test_req_demo_stamp_1_stamp_sits_above_the_headline_when_narrow(
    tmp_path: Path, width: int
) -> None:
    with running_app(tmp_path / "output", hosted_demo=True) as url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": width, "height": 1000})
        page.goto(f"{url}/demo")
        stamp = page.locator(".decision-answer").evaluate(STAMP_BOX)
        assert stamp["display"] != "none"
        assert stamp["content"] == '"LEAD\\a CANDIDATE"'
        bounds = rotated_bounds(stamp)
        eyebrow = page.locator(".decision-answer .eyebrow").bounding_box()
        assert eyebrow is not None
        assert bounds[3] <= eyebrow["y"]
        assert bounds[3] <= headline_box(page)[1]
        answer = page.locator(".decision-answer").bounding_box()
        assert answer is not None
        assert answer["x"] <= bounds[0] and bounds[2] <= answer["x"] + answer["width"]
        assert page.evaluate(
            "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
        )
        browser.close()


SECTION_GUTTERS = """() => Array.from(document.querySelectorAll('main > section')).map(section => {
    const box = section.getBoundingClientRect();
    const style = getComputedStyle(section);
    return {
        name: section.className,
        left: box.left + parseFloat(style.paddingLeft),
        right: box.right - parseFloat(style.paddingRight),
    };
})"""


@pytest.mark.parametrize("width", [1024, 1606])
def test_req_demo_gutter_1_sections_line_up_with_the_ranking_gutter(
    tmp_path: Path, width: int
) -> None:
    with running_app(tmp_path / "output", hosted_demo=True) as url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": width, "height": 1000})
        page.goto(f"{url}/demo")
        sections = {item["name"]: item for item in page.evaluate(SECTION_GUTTERS)}
        ranking = sections["ranking"]
        assert ranking["left"] > 0
        for name, section in sections.items():
            assert math.isclose(section["left"], ranking["left"], abs_tol=1), name
            assert math.isclose(section["right"], ranking["right"], abs_tol=1), name
        browser.close()
