"""BUI-2163: docs/limitations.md names the shipped catalog and the match bands (PRD G3, FR9)."""

from __future__ import annotations

import csv
import gzip
import json
import re
from pathlib import Path

from lifescape.discovery import GOOD_MATCH_FLOOR, STRONG_MATCH_FLOOR, match_band

ROOT = Path(__file__).resolve().parents[1]
LIMITATIONS = ROOT / "docs" / "limitations.md"
DATA = ROOT / "src" / "lifescape" / "data"


def limitations_text() -> str:
    return " ".join(LIMITATIONS.read_text(encoding="utf-8").split())


def test_bui_2163_limitations_name_the_packaged_catalog_version() -> None:
    manifest = json.loads((DATA / "place-catalog.manifest.json").read_text(encoding="utf-8"))
    version = manifest["catalog_version"]
    text = limitations_text()
    assert f"`{version}`" in text, version
    named = set(re.findall(r"`(us-places-[^`]+)`", text))
    assert named == {version}, named


def test_bui_2163_limitations_place_count_matches_the_catalog() -> None:
    with gzip.open(DATA / "place-catalog.csv.gz", "rt", encoding="utf-8", newline="") as handle:
        served = sum(
            1
            for row in csv.DictReader(handle)
            if row["population"] and float(row["population"]) >= 2500
        )
    assert f"({served:,} places in catalog" in limitations_text(), served


def test_bui_2163_limitations_do_not_call_matches_percentages() -> None:
    text = limitations_text()
    assert not re.search(r"match percent", text, re.IGNORECASE)


def test_bui_2163_limitations_describe_the_match_bands() -> None:
    text = limitations_text()
    bands = [match_band(STRONG_MATCH_FLOOR), match_band(GOOD_MATCH_FLOOR), match_band(0.0)]
    assert f"The {bands[0]}, {bands[1]}, and {bands[2]} match bands" in text, bands
    assert "not probabilities or quality scores" in text
