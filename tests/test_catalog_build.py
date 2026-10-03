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
    rows = build.build_rows(gazetteer, {"all": {"3700001": values}})

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
