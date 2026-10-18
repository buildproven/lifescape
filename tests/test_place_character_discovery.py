"""Place-character discovery tests (BUI-1396; PRD G1, G3, FR4, FR5).

Example Traverse City, MI once returned Golden Valley, MN, an inland Minneapolis suburb, first.
Water proximity, elevation, and large-metro context now separate lake towns from metro suburbs.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import io

import pytest

from lifescape.discovery import (
    CATALOG_COLUMNS,
    SCORED_FIELDS,
    STATE_REGIONS,
    CatalogUnavailableError,
    DiscoveryService,
    Place,
    PlaceCatalog,
    SearchProfile,
    format_value,
    load_catalog,
    parse_catalog,
)

FIELD_META = {
    field: {"label": field.replace("_", " ").title(), "unit": "percent", "definition": field}
    for field in SCORED_FIELDS
}
BOUNDS = {field: {"lower": 0.0, "upper": 100.0} for field in SCORED_FIELDS}
BOUNDS["population"]["upper"] = 10000.0
BOUNDS["median_home_value"]["upper"] = 1000.0
CHARACTER_BOUNDS = {
    **BOUNDS,
    "elevation_ft": {"lower": 0.0, "upper": 2000.0},
    "water_distance_mi": {"lower": 0.0, "upper": 100.0},
    "large_metro_distance_mi": {"lower": 0.0, "upper": 200.0},
    "large_metro_suburb": {"lower": 0.0, "upper": 1.0},
}


def build_catalog(rows: dict[str, tuple[str, str, float | None, ...]]) -> PlaceCatalog:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CATALOG_COLUMNS)
    for place_id, (name, state, *values) in rows.items():
        writer.writerow(
            [place_id, name, state, STATE_REGIONS[state]]
            + ["" if value is None else value for value in values]
            + [""] * (len(SCORED_FIELDS) - len(values))
            + ["1.0", "0", "0", ""]
        )
    compressed = gzip.compress(buffer.getvalue().encode(), mtime=0)
    manifest = {
        "catalog_version": "synthetic-fixture-v1",
        "data_date": "2000-01-01",
        "fields": FIELD_META,
        "bounds": CHARACTER_BOUNDS,
        "row_count": len(rows),
        "output_sha256": hashlib.sha256(compressed).hexdigest(),
    }
    return parse_catalog(compressed, manifest)


def ids(result: dict) -> list[str]:
    return [item["place_id"] for item in result["recommendations"]]


# id: (name, state, six Census values, elevation ft, water mi, metro mi, suburb flag)
LAKE_TOWNS: dict[str, tuple[str, str, float | None, ...]] = {
    "LAKE": ("Lakeport", "MI", 15000, 300, 40, 10, 40, 25, None, None, None, None, 600, 2, 150, 0),
    # Same Census numbers as the example, but an inland suburb of a 1M+ metro.
    "SUB": (
        "Golden Valley",
        "MN",
        15000,
        300,
        40,
        10,
        40,
        25,
        None,
        None,
        None,
        None,
        900,
        300,
        5,
        1,
    ),
    # Slightly different Census numbers, but on the water and far from a big metro.
    "SHORE": ("Shoreham", "WI", 15000, 350, 45, 10, 45, 25, None, None, None, None, 650, 5, 120, 0),
}


def test_lake_town_example_does_not_rank_an_inland_metro_suburb_first() -> None:
    service = DiscoveryService(build_catalog(LAKE_TOWNS))

    result = service.search(SearchProfile(exemplars=("LAKE",)))

    assert ids(result) == ["SHORE", "SUB"]
    fields = {c["field"] for c in result["recommendations"][0]["components"]}
    assert {"water_distance_mi", "large_metro_distance_mi", "large_metro_suburb"} <= fields


def test_place_character_values_are_read_and_a_negative_elevation_is_allowed() -> None:
    rows = {
        **LAKE_TOWNS,
        "SALTON": (
            "Salton City",
            "CA",
            15000,
            300,
            40,
            10,
            40,
            25,
            None,
            None,
            None,
            None,
            -230,
            40,
            90,
            0,
        ),
    }
    catalog = build_catalog(rows)

    assert catalog.places["SALTON"].values["elevation_ft"] == -230
    assert catalog.places["SUB"].values["large_metro_suburb"] == 1
    assert catalog.places["LAKE"].values["water_distance_mi"] == 2
    with pytest.raises(CatalogUnavailableError, match="nonnegative"):
        build_catalog(
            {"X": ("Bad", "CA", 15000, 300, 40, 10, 40, 25, None, None, None, None, 0, -1, 0, 0)}
        )


def test_place_character_units_read_naturally() -> None:
    assert format_value("feet above sea level", 1234.4) == "1,234 ft above sea level"
    assert format_value("miles", 12.4) == "12 miles"
    assert format_value("yes or no", 1) == "Yes"
    assert format_value("yes or no", 0) == "No"


@pytest.fixture(scope="module")
def real() -> PlaceCatalog:
    return load_catalog()


def test_traverse_city_example_ranks_water_adjacent_towns_above_inland_metro_suburbs(
    real: PlaceCatalog,
) -> None:
    """BUI-1396: Golden Valley, MN (inland, 5 miles from Minneapolis) was once #1."""
    traverse = real.lookup("Traverse City, MI")[0]

    result = DiscoveryService(real).search(SearchProfile(exemplars=(traverse.place_id,)), limit=50)

    places = [real.places[item["place_id"]] for item in result["recommendations"]]

    def inland_near_metro(place: Place) -> bool:
        water, metro = place.values["water_distance_mi"], place.values["large_metro_distance_mi"]
        return water is not None and metro is not None and water > 25 and metro < 15

    def water_adjacent_small_town(place: Place) -> bool:
        water = place.values["water_distance_mi"]
        return water is not None and water <= 10 and (place.population or 0) < 50_000

    assert places[0].values["water_distance_mi"] is not None
    assert places[0].values["water_distance_mi"] <= 10
    shore_ranks = [rank for rank, place in enumerate(places) if water_adjacent_small_town(place)]
    suburb_ranks = [rank for rank, place in enumerate(places) if inland_near_metro(place)]
    assert len(shore_ranks) >= 5
    assert all(rank > shore_ranks[4] for rank in suburb_ranks)
    assert "Golden Valley, MN" not in [place.label for place in places[:10]]


def test_shipped_place_character_values_are_plausible_and_cover_the_floor(
    real: PlaceCatalog,
) -> None:
    traverse = real.lookup("Traverse City, MI")[0]
    golden_valley = real.lookup("Golden Valley, MN")[0]
    minneapolis = real.lookup("Minneapolis, MN")[0]

    assert traverse.values["elevation_ft"] == 597
    assert traverse.values["water_distance_mi"] is not None
    assert traverse.values["water_distance_mi"] < 5
    assert traverse.values["large_metro_suburb"] == 0
    assert golden_valley.values["large_metro_suburb"] == 1
    assert golden_valley.values["water_distance_mi"] is not None
    assert golden_valley.values["water_distance_mi"] > 100
    assert golden_valley.values["large_metro_distance_mi"] is not None
    assert golden_valley.values["large_metro_distance_mi"] < 15
    assert minneapolis.values["large_metro_distance_mi"] == 0
    assert minneapolis.values["large_metro_suburb"] == 0
    below_sea_level = [
        place for place in real.places.values() if (place.values["elevation_ft"] or 0) < 0
    ]
    assert below_sea_level  # elevation is signed; every other quality is not
    for field in ("elevation_ft", "water_distance_mi", "large_metro_distance_mi"):
        assert real.manifest["coverage"][field]["share"] >= 0.8
