"""Match bands and one-line feel summaries (BUI-1397; PRD G3, FR9)."""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import re

import pytest

from lifescape.discovery import (
    CATALOG_COLUMNS,
    SCORED_FIELDS,
    STATE_REGIONS,
    DiscoveryService,
    PlaceCatalog,
    SearchProfile,
    match_band,
    parse_catalog,
)

# Same hand-computed fixture shape as tests/test_discovery.py: bounds 0-100 for every field except
# population (0-10,000) and median home value (0-1,000). Values follow SCORED_FIELDS order:
# population, home value, density, car-light, college, older, freezing nights, hot days, ...
FIELD_META = {
    field: {"label": field.replace("_", " ").title(), "unit": "percent", "definition": field}
    for field in SCORED_FIELDS
}
BOUNDS = {field: {"lower": 0.0, "upper": 100.0} for field in SCORED_FIELDS}
BOUNDS["population"]["upper"] = 10000.0
BOUNDS["median_home_value"]["upper"] = 1000.0
ROWS: dict[str, tuple[str, str, float | None, ...]] = {
    "EX": ("Example", "NC", 5000, 500, 50, 20, 40, 30),
    "A": ("Alpha", "NC", 5000, 500, 50, 20, 40, 30),
    "B": ("Bravo", "MI", 7500, 400, 60, 30, 50, 20),
    "C": ("Charlie", "OR", 5000, None, None, 20, 40, 30),
    "H": ("Hotel", "MI", 7500, 900, 60, 30, 50, 20),
}


TEMPERATURE_SOURCE = '{"temperature": ["US1", "Station One", 3.5]}'


def build_catalog(rows: dict[str, tuple[str, str, float | None, ...]]) -> PlaceCatalog:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CATALOG_COLUMNS)
    for place_id, (name, state, *values) in rows.items():
        writer.writerow(
            [place_id, name, state, STATE_REGIONS[state]]
            + ["" if value is None else value for value in values]
            + [""] * (len(SCORED_FIELDS) - len(values))
            + ["1.0", "0", "0", TEMPERATURE_SOURCE if place_id == "P" else ""]
        )
    compressed = gzip.compress(buffer.getvalue().encode(), mtime=0)
    manifest = {
        "catalog_version": "synthetic-fixture-v1",
        "data_date": "2000-01-01",
        "fields": FIELD_META,
        "bounds": BOUNDS,
        "row_count": len(rows),
        "output_sha256": hashlib.sha256(compressed).hexdigest(),
    }
    return parse_catalog(compressed, manifest)


def _service(rows: dict[str, tuple[str, str, float | None, ...]] | None = None) -> DiscoveryService:
    return DiscoveryService(build_catalog(ROWS if rows is None else rows))


def _totals(service: DiscoveryService) -> dict[str, dict]:
    result = service.search(SearchProfile(exemplars=("EX",)))
    return {item["place_id"]: item for item in result["recommendations"]}


def test_req_card_1_bands_follow_total_match_thresholds() -> None:
    items = _totals(_service())
    # A 1.0, B 0.875, H 0.825, C 0.666667 (hand-computed in tests/test_discovery.py).
    assert items["A"]["total_match"] == 1.0
    assert items["A"]["match_band"] == "Strong"
    assert items["B"]["total_match"] == 0.875
    assert items["B"]["match_band"] == "Strong"
    assert items["H"]["total_match"] == 0.825
    assert items["H"]["match_band"] == "Good"
    assert items["C"]["total_match"] == 0.666667
    assert items["C"]["match_band"] == "Possible"


@pytest.mark.parametrize(
    ("total", "band"),
    [
        (1.0, "Strong"),
        (0.875, "Strong"),
        (0.85, "Strong"),
        (0.849999, "Good"),
        (0.825, "Good"),
        (0.7, "Good"),
        (0.699999, "Possible"),
        (0.6667, "Possible"),
        (0.0, "Possible"),
    ],
)
def test_req_card_1_band_edges(total: float, band: str) -> None:
    assert match_band(total) == band


def test_req_card_1_total_and_percent_are_unchanged() -> None:
    items = _totals(_service())
    for item in items.values():
        assert item["match_percent"] == round(item["total_match"] * 100)
    assert items["B"]["match_percent"] == 88
    assert items["H"]["match_percent"] == 82
    assert items["C"]["match_percent"] == 67


def test_req_card_2_feel_summary_is_one_nonempty_line_without_percent_or_match() -> None:
    items = _totals(_service())
    assert items
    for item in items.values():
        summary = item["feel_summary"]
        assert isinstance(summary, str) and summary.strip()
        assert "\n" not in summary and "\r" not in summary
        assert "%" not in summary
        assert not re.search(r"match", summary, re.IGNORECASE)


def test_req_card_2_feel_summary_never_states_a_missing_quality() -> None:
    rows = dict(ROWS)
    # A has no water distance. Wet is the same town with a known 3 mile distance to water.
    rows["Wet"] = ("Wetland", "NC", 5000, 500, 50, 20, 40, 30, None, None, None, None, None, 3)
    items = _totals(_service(rows))
    assert items["A"]["fields"][11]["field"] == "water_distance_mi"
    assert items["A"]["fields"][11]["candidate_value"] is None
    assert not re.search(r"water|ocean|lake|shore|coast", items["A"]["feel_summary"], re.I)
    assert items["Wet"]["fields"][11]["candidate_value"] == 3
    assert "Great Lake" in items["Wet"]["feel_summary"]
    assert "winter" not in items["Wet"]["feel_summary"]
    assert "metro" not in items["Wet"]["feel_summary"]


def test_req_card_2_feel_summary_describes_known_climate_and_metro_values() -> None:
    rows = {
        "EX": ROWS["EX"],
        "P": (
            "Pine",
            "NC",
            5000,
            500,
            50,
            20,
            40,
            30,
            10,  # freezing nights per year
            70,  # hot days per year
        ),
    }
    items = _totals(_service(rows))
    summary = items["P"]["feel_summary"]
    assert summary.startswith("A small town of about 5,000 people")
    assert "mild winters" in summary and "hot summers" in summary
    assert "snow" not in summary
