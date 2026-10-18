"""Catalog build derivation tests (PRD FR4, FR5; ADR sentinel and derivation policy)."""

from __future__ import annotations

import importlib.util
import io
import json
import zipfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/build_place_catalog.py"
spec = importlib.util.spec_from_file_location("build_place_catalog", SCRIPT)
assert spec and spec.loader
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("123", 123.0),
        ("", None),
        ("  ", None),
        ("-666666666", None),
        ("-999999999", None),
        ("abc", None),
        ("0", 0.0),
    ],
)
def test_clean_value_turns_blanks_and_negative_sentinels_into_null(
    raw: str, expected: float | None
) -> None:
    assert build.clean_value(raw) == expected


def test_share_requires_every_component_and_a_positive_total() -> None:
    row = {"T": 200.0, "A": 10.0, "B": 30.0}

    assert build.share(row, ("A", "B"), "T") == 20.0
    assert build.share({**row, "B": None}, ("A", "B"), "T") is None
    assert build.share({**row, "T": 0.0}, ("A", "B"), "T") is None
    assert build.share({**row, "T": None}, ("A", "B"), "T") is None
    assert build.share({"T": 10.0, "A": 30.0}, ("A",), "T") == 100.0


def test_acs_table_keeps_only_places_and_estimate_columns() -> None:
    data = (
        b"GEO_ID|B01003_E001|B01003_M001\n"
        b"0100000US|1000|5\n"
        b"1600000US0100100|2500|10\n"
        b"1600000US0100124|-666666666|-222222222\n"
    )

    assert build.read_acs_table(data) == {
        "0100100": {"B01003_E001": 2500.0},
        "0100124": {"B01003_E001": None},
    }


def test_display_names_drop_legal_suffixes() -> None:
    assert build.display_name("Traverse City city") == "Traverse City"
    assert build.display_name("Abanda CDP") == "Abanda"
    assert build.display_name("Boone town") == "Boone"
    assert build.display_name("Plain") == "Plain"


def _gazetteer(rows: list[str]) -> bytes:
    columns = "USPS GEOID ANSICODE NAME LSAD FUNCSTAT ALAND AWATER ALAND_SQMI AWATER_SQMI"
    header = "\t".join([*columns.split(), "INTPTLAT", "INTPTLONG  "])
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("gaz.txt", "\n".join([header, *rows]) + "\n")
    return buffer.getvalue()


def test_rows_derive_fields_disambiguate_names_and_skip_territories() -> None:
    gazetteer = build.read_gazetteer(
        _gazetteer(
            [
                "NC\t3700001\t1\tMidway town\t43\tA\t1\t1\t10.0\t0\t35.0\t-80.0",
                "NC\t3700002\t1\tMidway CDP\t57\tS\t1\t1\t0\t0\t35.1\t-80.1",
                "PR\t7200001\t1\tAdjuntas zona urbana\t57\tS\t1\t1\t1.0\t0\t18.0\t-66.0",
            ]
        )
    )
    values = {
        "B01003_E001": 5000.0,
        "B25077_E001": 250000.0,
        "B08301_E001": 100.0,
        "B08301_E010": 5.0,
        "B08301_E018": 1.0,
        "B08301_E019": 2.0,
        "B08301_E021": 12.0,
        "B15003_E001": 200.0,
        "B15003_E022": 40.0,
        "B15003_E023": 20.0,
        "B15003_E024": 5.0,
        "B15003_E025": 5.0,
        "B01001_E001": 5000.0,
        **dict.fromkeys(build.OLDER_ADULT_VARIABLES, 100.0),
    }
    rows = build.build_rows(gazetteer, {"all": {"3700001": values}}, [])

    assert [row["place_id"] for row in rows] == ["3700001", "3700002"]
    town, cdp = rows
    assert (town["name"], cdp["name"]) == ("Midway (town)", "Midway (CDP)")
    assert town["population_density"] == "500.0"
    assert town["car_light_commute_share"] == "20.00"
    assert town["college_educated_share"] == "35.00"
    assert town["older_adult_share"] == "24.00"
    assert town["median_home_value"] == "250000"
    assert town["region"] == "South"
    assert cdp["population"] == "" and cdp["population_density"] == ""


def test_encoding_is_deterministic() -> None:
    rows = [dict.fromkeys(build.CATALOG_COLUMNS, "1")]

    assert build.encode_catalog(rows) == build.encode_catalog(rows)


def _station(station_id: str, lat: float, lon: float, **variables: float) -> build.Station:
    return (station_id, station_id.title(), lat, lon, variables)


def test_climate_join_uses_nearest_qualifying_station_within_radius() -> None:
    temperature = ("ANN-TMIN-AVGNDS-LSTH032", "ANN-TMAX-AVGNDS-GRTH090")
    full = {"ANN-TMIN-AVGNDS-LSTH032": 100.0, "ANN-TMAX-AVGNDS-GRTH090": 10.0}
    stations = [
        _station("USNEAR", 35.0, -80.0, **{"ANN-TMIN-AVGNDS-LSTH032": 90.0}),  # lacks hot days
        _station("USFULL", 35.1, -80.0, **full),
        _station("USFAR", 36.5, -80.0, **full),
    ]
    index = build.StationIndex(stations, temperature)

    found = index.nearest(35.0, -80.0)
    assert found is not None and found[0][0] == "USFULL"
    assert 6.0 < found[1] < 8.0  # 0.1 degrees of latitude is about 6.9 miles
    assert index.nearest(37.5, -80.0) is None


def test_climate_cells_stay_empty_when_no_station_qualifies() -> None:
    place = {"INTPTLAT": "35.0", "INTPTLONG": "-80.0"}
    indexes = {
        group: build.StationIndex([], tuple(variable for _, variable in fields))
        for group, fields in build.CLIMATE_GROUPS.items()
    }

    cells, sources = build.climate_for(place, indexes)

    assert set(cells.values()) == {""}
    assert sources == ""


def test_climate_snowfall_is_missing_not_zero_without_a_snow_station() -> None:
    place = {"INTPTLAT": "35.0", "INTPTLONG": "-80.0"}
    stations = [_station("USRAIN", 35.0, -80.0, **{"ANN-PRCP-NORMAL": 44.4})]
    indexes = {
        group: build.StationIndex(stations, tuple(variable for _, variable in fields))
        for group, fields in build.CLIMATE_GROUPS.items()
    }

    cells, sources = build.climate_for(place, indexes)

    assert cells["annual_precip_in"] == "44.4"
    assert cells["annual_snowfall_in"] == "" and cells["hot_days_per_year"] == ""
    assert '"precipitation":["USRAIN","Usrain",0.0]' in sources
    assert "snowfall" not in sources


def test_climate_join_wraps_across_the_antimeridian() -> None:
    variables = ("ANN-PRCP-NORMAL",)
    stations = [_station("USWEST", 52.9, -179.9, **{"ANN-PRCP-NORMAL": 30.0})]
    index = build.StationIndex(stations, variables)

    found = index.nearest(52.9, 179.9)

    assert found is not None and found[0][0] == "USWEST"
    assert found[1] < 10


# -- place-character qualities (BUI-1396; PRD FR4, FR5) -----------------------------------


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


def _place(place_id: str, name: str, state: str, lat: float, lon: float, population: float | None):
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
