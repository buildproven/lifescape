"""BUI-1721: the 1.1.0 release notes describe match bands, not match percentages (PRD G3, FR9)."""

from __future__ import annotations

import re
from pathlib import Path

from lifescape.discovery import GOOD_MATCH_FLOOR, STRONG_MATCH_FLOOR

CHANGELOG = Path(__file__).resolve().parents[1] / "CHANGELOG.md"


def release_notes(version: str) -> str:
    text = CHANGELOG.read_text(encoding="utf-8")
    match = re.search(rf"^## {re.escape(version)}\b.*?(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    assert match, f"CHANGELOG.md has no {version} section"
    return match.group(0)


def test_bui_1721_release_notes_do_not_claim_match_percentages() -> None:
    notes = release_notes("1.1.0")
    assert not re.search(r"match percent", notes, re.IGNORECASE), notes


def test_bui_1721_comparison_entry_names_the_discovery_match_band() -> None:
    notes = release_notes("1.1.0")
    entry = re.search(r"^- \*\*Side-by-side comparison\.\*\*.*?(?=^- |\Z)", notes, re.M | re.S)
    assert entry, "1.1.0 notes lost the side-by-side comparison entry"
    assert "band" in entry.group(0).lower(), entry.group(0)


def test_bui_1721_release_notes_describe_bands_and_card_summaries() -> None:
    notes = release_notes("1.1.0")
    entry = re.search(r"^- \*\*Match bands.*?(?=^- |^### |\Z)", notes, re.M | re.S)
    assert entry, "1.1.0 notes have no match-band entry"
    text = " ".join(entry.group(0).split())
    assert "BUI-1397" in text
    for band in ("Strong", "Good", "Possible"):
        assert f"**{band}**" in text, band
    assert f"{STRONG_MATCH_FLOOR:.2f}" in text
    assert f"{GOOD_MATCH_FLOOR:.2f}" in text
    assert "summary" in text.lower()
    assert "never imputed" in text or "never inferred" in text
