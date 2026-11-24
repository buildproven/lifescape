"""BUI-2163: docs/limitations.md names match bands and the shipped catalog (PRD G3, FR9)."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIMITATIONS = ROOT / "docs" / "limitations.md"
MANIFEST = ROOT / "src" / "lifescape" / "data" / "place-catalog.manifest.json"


def limitations_text() -> str:
    return " ".join(LIMITATIONS.read_text(encoding="utf-8").split())


def test_bui_2163_limitations_do_not_claim_match_percentages() -> None:
    assert not re.search(r"match percent", limitations_text(), re.IGNORECASE)


def test_bui_2163_limitations_name_the_match_bands() -> None:
    text = limitations_text()
    for band in ("Strong", "Good", "Possible"):
        assert f"**{band}**" in text, band
    assert "not probabilities or quality scores" in text


def test_bui_2163_limitations_name_the_shipped_catalog_version() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    text = limitations_text()
    assert f"`{manifest['catalog_version']}`" in text
    named = set(re.findall(r"`(us-places-[^`]+)`", text))
    assert named == {manifest["catalog_version"]}, named
