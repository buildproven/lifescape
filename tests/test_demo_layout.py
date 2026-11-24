"""Hosted demo layout guards (PRD FR16, BUI-2209).

The demo stamp once rendered the literal text ``LEAD\\A CANDIDATE`` on top of the headline, and the
"How this field was found." section ran edge to edge without the page gutter. These tests read
``demo.css`` and ``demo.html`` directly so a regression fails without a browser.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
DEMO_CSS = REPOSITORY / "src/lifescape/static/demo.css"
DEMO_HTML = REPOSITORY / "src/lifescape/templates/demo.html"
STAMP = ".decision-answer::after"
# The call-to-action band is full bleed (its clay background spans the viewport); it keeps the
# page gutter with padding derived from the same 1450px page width instead of the shared rule.
FULL_BLEED_SECTIONS = {"demo-cta"}
VOID_TAGS = {"br", "img", "input", "meta", "link", "source", "hr", "wbr"}


def parse_rules(css: str) -> list[tuple[str | None, str, dict[str, str]]]:
    """Return ``(media condition or None, selector, declarations)`` for every rule in order."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    rules: list[tuple[str | None, str, dict[str, str]]] = []

    def walk(text: str, media: str | None) -> None:
        position = 0
        while True:
            opening = text.find("{", position)
            if opening == -1:
                return
            prelude = text[position:opening].strip()
            depth, index = 1, opening + 1
            while depth:
                if text[index] == "{":
                    depth += 1
                elif text[index] == "}":
                    depth -= 1
                index += 1
            body = text[opening + 1 : index - 1]
            if prelude.startswith("@media"):
                walk(body, prelude.removeprefix("@media").strip())
            else:
                declarations: dict[str, str] = {}
                for declaration in re.split(r";(?=(?:[^\"]*\"[^\"]*\")*[^\"]*$)", body):
                    name, colon, value = declaration.partition(":")
                    if colon:
                        declarations[name.strip()] = " ".join(value.split())
                for selector in prelude.split(","):
                    rules.append((media, " ".join(selector.split()), declarations))
            position = index

    walk(css, None)
    return rules


def declarations_for(selector: str, media: str | None = None) -> dict[str, str]:
    """Merge every declaration that applies to ``selector`` in ``media``, in source order."""
    merged: dict[str, str] = {}
    for rule_media, rule_selector, declarations in parse_rules(DEMO_CSS.read_text("utf-8")):
        if rule_selector == selector and rule_media == media:
            merged.update(declarations)
    return merged


def horizontal_padding(declarations: dict[str, str]) -> tuple[str, str]:
    """Return ``(right, left)`` padding from the ``padding`` shorthand (CSS 1 to 4 value form)."""
    parts = declarations.get("padding", "0").split()
    right = parts[1] if len(parts) > 1 else parts[0]
    left = parts[3] if len(parts) > 3 else right
    return right, left


class SectionClasses(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.depth = 0
        self.in_main = False
        self.sections: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "main":
            self.in_main = True
            self.depth = 0
            return
        if not self.in_main:
            return
        if tag == "section" and self.depth == 0:
            self.sections.append(dict(attrs).get("class") or "")
        if tag not in VOID_TAGS:
            self.depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "main":
            self.in_main = False
        elif self.in_main and tag not in VOID_TAGS:
            self.depth -= 1


def test_bui_2209_fr16_no_content_value_escapes_the_line_break_backslash() -> None:
    contents = [
        (selector, declarations["content"])
        for _, selector, declarations in parse_rules(DEMO_CSS.read_text("utf-8"))
        if "content" in declarations
    ]

    assert contents
    assert [(s, value) for s, value in contents if "\\\\A" in value] == []


def test_bui_2209_fr16_stamp_reads_lead_candidate_on_two_lines() -> None:
    stamp = declarations_for(STAMP)

    assert stamp["content"] == '"LEAD\\A CANDIDATE"'
    assert stamp["white-space"] == "pre"


def test_bui_2209_fr16_stamp_sits_in_flow_above_the_headline_at_every_width() -> None:
    stamp = declarations_for(STAMP)

    # In normal flow and first in the flex column, the stamp occupies its own row above the
    # eyebrow and headline, so it cannot overlap them at any viewport width.
    assert declarations_for(".decision-answer")["display"] == "flex"
    assert declarations_for(".decision-answer")["flex-direction"] == "column"
    assert stamp.get("position", "static") == "static"
    assert stamp["order"] == "-1"
    for media, selector, declarations in parse_rules(DEMO_CSS.read_text("utf-8")):
        if selector == STAMP and media is not None:
            assert declarations.get("display") != "none", media
            assert declarations.get("position", "static") == "static", media
            assert "order" not in declarations, media


def test_bui_2209_fr16_path_section_uses_the_ranking_gutter() -> None:
    path, ranking = declarations_for(".path"), declarations_for(".ranking")

    assert ranking["width"] == "var(--page)"
    assert path["width"] == ranking["width"]
    assert path["margin"] == ranking["margin"] == "0 auto"
    assert horizontal_padding(path) == horizontal_padding(ranking)
    for media in {m for m, _, _ in parse_rules(DEMO_CSS.read_text("utf-8")) if m is not None}:
        overrides = declarations_for(".path", media)
        assert "width" not in overrides and "margin" not in overrides, media
        if "padding" in overrides:
            assert horizontal_padding(overrides) == horizontal_padding(
                declarations_for(".ranking", media)
            ), media


def test_bui_2209_fr16_every_top_level_section_keeps_the_page_gutter() -> None:
    parser = SectionClasses()
    parser.feed(DEMO_HTML.read_text("utf-8"))

    assert "path" in parser.sections and "ranking" in parser.sections
    for classes in parser.sections:
        names = classes.split()
        assert names, "top-level demo section without a class"
        if FULL_BLEED_SECTIONS.intersection(names):
            padding = declarations_for(f".{names[0]}")["padding"]
            assert "1450px" in padding, classes
            continue
        gutter = [
            name
            for name in names
            if declarations_for(f".{name}").get("width") == "var(--page)"
            and declarations_for(f".{name}").get("margin") == "0 auto"
        ]
        assert gutter, f"section .{classes} has no page gutter"
