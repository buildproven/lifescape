"""Place-character catalog build tests (BUI-1396; PRD FR4, FR5)."""

from __future__ import annotations

import importlib.util
import io
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/build_place_catalog.py"
spec = importlib.util.spec_from_file_location("build_place_catalog", SCRIPT)
assert spec and spec.loader
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


def _geojson_lines(*lines: list[list[float]]) -> bytes:
    features = [
        {"type": "Feature", "properties": {}, "geometry": {"type": "LineString", "coordinates": c}}
        for c in lines
    ]
    return json.dumps({"features": features}).encode()


def _lakes(names: tuple[str, ...]) -> bytes:
    ring = [[-86.0, 44.0], [-86.0, 45.0], [-85.0, 45.0], [-86.0, 44.0]]
    features = [
        {
            "type": "Feature",
            "properties": {"name": name},
            "geometry": {"type": "Polygon", "coordinates": [ring]},
        }
        for name in (*names, "Lake Not Great")
    ]
    return json.dumps({"features": features}).encode()


def test_shore_distance_is_exact_near_a_coast_and_far_inland() -> None:
    coast = _geojson_lines([[-85.0, 30.0], [-85.0, 31.0]], [[-120.0, 34.0], [-120.0, 35.0]])
    lines = build.read_shorelines(coast, _lakes(build.GREAT_LAKES))
    index = build.ShoreIndex(lines)

    found = index.distances(
        [("beach", 30.5, -84.9), ("inland", 38.0, -100.0), ("mid-segment", 30.5, -85.0)]
    )

    assert found["beach"] == pytest.approx(0.1 * 69.093 * 0.8613, abs=0.3)  # 0.1° of longitude
    assert found["mid-segment"] == pytest.approx(0.0, abs=0.01)
    nearest_vertex = min(
        build.miles_between(38.0, -100.0, lat, lon)
        for lat, lon in ((35.0, -120.0), (31.0, -85.0), (44.0, -86.0))  # coasts and the lake
    )
    assert found["inland"] == pytest.approx(nearest_vertex, abs=3.0)


def test_shorelines_require_every_great_lake_and_ignore_other_lakes() -> None:
    coast = _geojson_lines([[-85.0, 30.0], [-85.0, 31.0]])

    with pytest.raises(SystemExit, match="Lake Ontario"):
        build.read_shorelines(coast, _lakes(build.GREAT_LAKES[:-1]))

    lines = build.read_shorelines(coast, _lakes(build.GREAT_LAKES))
    assert len(lines) == 1 + len(build.GREAT_LAKES)


def test_shorelines_drop_vertices_that_cannot_serve_u_s_places() -> None:
    coast = _geojson_lines([[10.0, 50.0], [11.0, 50.0]], [[-85.0, 30.0], [-85.0, 31.0]])

    lines = build.read_shorelines(coast, _lakes(build.GREAT_LAKES))

    assert [(-85.0, 30.0)] not in lines
    assert all(vertex[1] < 0 for line in lines for vertex in line)


def test_gnis_elevation_joins_by_state_and_name_within_five_miles() -> None:
    rows = "\n".join(
        [
            "FEATURE_ID|FEATURE_NAME|FEATURE_CLASS|STATE_ALPHA|PRIM_LAT_DEC|PRIM_LONG_DEC|ELEV_IN_FT",
            "1|Saint Paul|Populated Place|MN|44.95|-93.09|800",
            "2|Traverse City|Populated Place|MI|44.76|-85.62|597",
            "3|Traverse City|Populated Place|MI|45.50|-85.62|999",
            "4|Traverse City|Lake|MI|44.76|-85.62|111",
            "5|Salton City|Populated Place|CA|33.30|-115.95|-230",
            "6|No Height|Populated Place|MI|44.0|-85.0|",
        ]
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("XX_Features_20210825.txt", "﻿" + rows)
        archive.writestr("README.txt", "ignored")
    elevations = build.read_gnis_elevations(buffer.getvalue())

    assert build.elevation_for("Traverse City", "MI", 44.77, -85.63, elevations) == "597"
    assert build.elevation_for("St. Paul", "MN", 44.95, -93.1, elevations) == "800"
    assert build.elevation_for("Salton City (CDP)", "CA", 33.3, -115.95, elevations) == "-230"
    assert build.elevation_for("Traverse City", "MI", 44.9, -85.62, elevations) == ""
    assert build.elevation_for("Traverse City", "OH", 44.76, -85.62, elevations) == ""
    assert build.elevation_for("No Height", "MI", 44.0, -85.0, elevations) == ""


def _cbsa_inputs() -> tuple[bytes, bytes]:
    header = "CSAFP\tGEOID\tNAME\tCBSA_TYPE\tINTPTLAT\tINTPTLONG   \n"
    rows = [
        "1\t10001\tBigton-Smallville, MN-WI Metro Area\t1\t45.0\t-93.0",
        "2\t10002\tSmallton, MI Metro Area\t1\t44.0\t-85.0",
        "3\t10003\tMicroville, MI Micro Area\t2\t44.5\t-85.5",
        "4\t10004\tNash-Dav--Murf--Frank, TN Metro Area\t1\t36.0\t-86.0",
        "5\t10005\tSan Juan, PR Metro Area\t1\t18.4\t-66.1",
    ]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("2024_Gaz_cbsa_national.txt", header + "\n".join(rows) + "\n")
    acs = (
        "GEO_ID|B01003_E001|B01003_M001\n"
        "310M700US10001|2500000|-555555555\n"
        "310M700US10002|900000|-555555555\n"
        "310M700US10003|2000000|-555555555\n"
        "310M700US10004|1100000|-555555555\n"
        "310M700US10005|2000000|-555555555\n"
        "1600000US2600100|5000|10\n"
    )
    return buffer.getvalue(), acs.encode()


def test_large_metros_are_metropolitan_areas_with_a_million_people() -> None:
    gazetteer, acs = _cbsa_inputs()

    metros = build.read_large_metros(gazetteer, acs)

    assert metros == [
        ("Bigton-Smallville, MN-WI Metro Area", ["Bigton", "Smallville"], ["MN", "WI"]),
        (
            "Nash-Dav--Murf--Frank, TN Metro Area",
            ["Nash-Dav", "Murf", "Frank"],
            ["TN"],
        ),
    ]


def _place(
    place_id: str, name: str, state: str, lat: float, lon: float, population: float | None
) -> dict[str, Any]:
    return {
        "id": place_id,
        "name": name,
        "state": state,
        "lat": lat,
        "lon": lon,
        "population": population,
    }


def test_metro_cores_pick_the_largest_same_named_place_in_the_metro_states() -> None:
    metros = [("Bigton-Smallville, MN-WI Metro Area", ["Bigton", "Smallville"], ["MN", "WI"])]
    places = [
        _place("big-mn", "Bigton", "MN", 45.0, -93.0, 400000),
        _place("big-mn-small", "Bigton", "MN", 46.0, -94.0, 3000),
        _place("big-tx", "Bigton", "TX", 30.0, -97.0, 900000),
        _place("small-wi", "Smallville city", "WI", 44.9, -92.0, 20000),
        _place("grove-mn", "Bigton Grove", "MN", 45.5, -93.5, 90000),
    ]

    cores, unmatched = build.metro_cores(metros, places)

    assert set(cores) == {"big-mn", "small-wi"}  # "Bigton Grove" is not "Bigton"
    assert unmatched == []
    assert build.metro_cores([("Ghost, MN Metro Area", ["Ghost"], ["MN"])], places)[1] == [
        "Ghost, MN Metro Area"
    ]


def test_character_cells_flag_suburbs_by_distance_and_keep_unknowns_blank() -> None:
    gazetteer, acs = _cbsa_inputs()
    geography = build.Geography(
        build.ShoreIndex(
            build.read_shorelines(
                _geojson_lines([[-86.5, 44.7], [-86.5, 44.8]]), _lakes(build.GREAT_LAKES)
            )
        ),
        {("MN", "bigton"): [(45.0, -93.0, 830.0)]},
        build.read_large_metros(gazetteer, acs)[:1],
    )
    places = [
        _place("core", "Bigton", "MN", 45.0, -93.0, 400000),
        _place("suburb", "Golden", "MN", 45.1, -93.2, 22000),
        _place("far", "Lakeside", "MI", 44.75, -86.4, 5000),
    ]

    cells = build.character_cells(places, geography)

    assert cells["core"]["large_metro_distance_mi"] == "0.0"
    assert cells["core"]["large_metro_suburb"] == "0"
    assert cells["core"]["elevation_ft"] == "830"
    assert cells["suburb"]["large_metro_suburb"] == "1"
    assert cells["suburb"]["elevation_ft"] == ""
    assert float(cells["suburb"]["large_metro_distance_mi"]) < build.METRO_SUBURB_RADIUS_MILES
    assert cells["far"]["large_metro_suburb"] == "0"
    assert float(cells["far"]["large_metro_distance_mi"]) > 300
    assert float(cells["far"]["water_distance_mi"]) < 10


def test_character_cells_fail_when_a_metro_title_city_matches_no_place() -> None:
    geography = build.Geography(
        build.ShoreIndex(
            build.read_shorelines(
                _geojson_lines([[-85.0, 30.0], [-85.0, 31.0]]), _lakes(build.GREAT_LAKES)
            )
        ),
        {},
        [("Ghost, MN Metro Area", ["Ghost"], ["MN"])],
    )

    with pytest.raises(SystemExit, match="Ghost"):
        build.character_cells([_place("a", "Other", "MN", 45.0, -93.0, 5000)], geography)
