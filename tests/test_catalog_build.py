"""Catalog build derivation tests (PRD FR4, FR5; ADR sentinel and derivation policy)."""

from __future__ import annotations

import importlib.util
import io
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
