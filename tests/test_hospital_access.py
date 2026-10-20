"""Hospital-access discovery quality tests (BUI-1111; PRD FR4, FR14, G5).

Straight-line miles to the nearest CMS emergency-capable hospital is Tier C discovery data. It
carries its facility as provenance, stays null when no hospital is known, and never reaches a gate.
"""

from __future__ import annotations

import ast
import csv
import gzip
import hashlib
import importlib.util
import io
import json
import random
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from lifescape.discovery import (
    CATALOG_COLUMNS,
    SCORED_FIELDS,
    STATE_REGIONS,
    CatalogUnavailableError,
    DiscoveryService,
    PlaceCatalog,
    SearchProfile,
    format_value,
    load_catalog,
    parse_catalog,
)
from lifescape.gates import evaluate_gates
from lifescape.models import GateDefinition, GateOperator, GateState, ObservationRecord
from lifescape.web import create_app

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/build_place_catalog.py"
spec = importlib.util.spec_from_file_location("build_place_catalog", SCRIPT)
assert spec and spec.loader
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)

CMS_HEADER = "Facility ID,Facility Name,State,ZIP Code,Emergency Services\n"


def _zcta_zip(rows: dict[str, tuple[float, float]]) -> bytes:
    lines = ["GEOID\tALAND\tAWATER\tALAND_SQMI\tAWATER_SQMI\tINTPTLAT\tINTPTLONG  "]
    lines += [f"{zcta}\t1\t1\t1.0\t1.0\t{lat}\t{lon}  " for zcta, (lat, lon) in rows.items()]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("2024_Gaz_zcta_national.txt", "\n".join(lines) + "\n")
    return buffer.getvalue()


def _cms(*rows: str, header: str = CMS_HEADER) -> bytes:
    return (header + "".join(row + "\n" for row in rows)).encode()


CENTROIDS = {"28001": (35.0, -80.0), "28002": (35.5, -80.0), "99501": (61.2, -149.9)}


def test_zcta_centroids_are_read_by_five_digit_code() -> None:
    assert build.read_zcta_centroids(_zcta_zip(CENTROIDS)) == CENTROIDS


def test_only_located_emergency_hospitals_in_catalog_states_are_kept() -> None:
    data = _cms(
        '010001,"ONE, MEDICAL CENTER",NC,28001,Yes',
        "010002,NO ER CLINIC,NC,28001,No",
        "010003,UNLOCATED HOSPITAL,NC,28999,Yes",
        "010004,PUERTO RICO HOSPITAL,PR,28001,Yes",
        "010005,ANCHORAGE HOSPITAL,AK,99501,Yes",
    )

    hospitals, counts = build.read_cms_hospitals(data, CENTROIDS)

    assert hospitals == [
        ("010001", "ONE, MEDICAL CENTER", 35.0, -80.0),
        ("010005", "ANCHORAGE HOSPITAL", 61.2, -149.9),
    ]
    assert counts == {"emergency_facilities": 3, "located": 2, "unlocated": 1}


def test_cms_file_with_a_repeated_facility_or_missing_column_stops_the_build() -> None:
    with pytest.raises(SystemExit, match="repeats facility 010001"):
        build.read_cms_hospitals(_cms("010001,A,NC,28001,Yes", "010001,B,NC,28001,Yes"), CENTROIDS)
    with pytest.raises(SystemExit, match="missing columns"):
        build.read_cms_hospitals(
            _cms("010001,A,NC,28001", header="Facility ID,Facility Name,State,ZIP Code\n"),
            CENTROIDS,
        )


def test_nearest_hospital_is_the_closest_and_ties_break_by_facility_id() -> None:
    index = build.HospitalIndex(
        [("B", "Bravo", 35.1, -80.0), ("A", "Alpha", 34.9, -80.0), ("C", "Far", 36.0, -80.0)]
    )

    found = index.nearest(35.0, -80.0)
    assert found is not None and found[0][0] == "A"  # equal distance, lower id
    assert found[1] == pytest.approx(6.9, abs=0.1)
    nearer = index.nearest(35.95, -80.0)
    assert nearer is not None and nearer[0][0] == "C"


def test_nearest_hospital_matches_brute_force_including_remote_and_antimeridian_places() -> None:
    generator = random.Random(1111)
    hospitals = [
        (f"H{n:03d}", f"Hospital {n}", generator.uniform(25, 49), generator.uniform(-124, -70))
        for n in range(120)
    ] + [
        ("AK1", "Anchorage", 61.2, -149.9),
        ("AL1", "Adak", 51.88, 179.6),
        ("HI1", "Honolulu", 21.3, -157.9),
    ]
    index = build.HospitalIndex(hospitals)
    points = [(generator.uniform(25, 49), generator.uniform(-124, -70)) for _ in range(150)] + [
        (52.0, -179.9),  # across the antimeridian from Adak
        (71.3, -156.7),  # far north, hundreds of miles from the nearest hospital
        (19.5, -155.5),
    ]
    for lat, lon in points:
        expected = min((build.miles_between(lat, lon, h[2], h[3]), h[0]) for h in hospitals)
        found = index.nearest(lat, lon)
        assert found is not None
        assert found[0][0] == expected[1] and found[1] == pytest.approx(expected[0]), (lat, lon)
    adak = index.nearest(52.0, -179.9)
    assert adak is not None and adak[0][0] == "AL1" and adak[1] < 50


def test_no_hospital_in_the_source_leaves_the_distance_and_facility_blank() -> None:
    assert build.HospitalIndex([]).nearest(35.0, -80.0) is None
    assert build.hospital_for(35.0, -80.0, build.HospitalIndex([])) == ("", "")
    assert build.hospital_for(35.0, -80.0, None) == ("", "")


def test_distance_cell_and_provenance_agree_and_keep_the_facility_name() -> None:
    index = build.HospitalIndex([("010001", 'ST. "LUKE\'S" CAFÉ HOSPITAL', 35.2, -80.0)])

    cell, source = build.hospital_for(35.0, -80.0, index)

    facility, name, miles = json.loads(source)
    assert (facility, name) == ("010001", 'ST. "LUKE\'S" CAFÉ HOSPITAL')
    assert cell == f"{miles:.1f}" and float(cell) == miles
    assert miles == pytest.approx(13.8, abs=0.2)


def _gazetteer_place(geoid: str, lat: str, lon: str) -> dict[str, str]:
    return {
        "GEOID": geoid,
        "USPS": "NC",
        "NAME": f"Town {geoid} town",
        "ALAND_SQMI": "10.0",
        "INTPTLAT": lat,
        "INTPTLONG": lon,
    }


def test_built_rows_record_the_hospital_and_leave_it_blank_without_an_index() -> None:
    places = [_gazetteer_place("3700001", "35.0", "-80.0")]
    index = build.HospitalIndex([("010001", "ONE MEDICAL CENTER", 35.1, -80.0)])

    (with_hospital,) = build.build_rows(places, {}, [], None, index)
    (without,) = build.build_rows(places, {}, [])

    assert with_hospital["hospital_distance_mi"] == "6.9"
    assert json.loads(with_hospital["hospital_source"]) == ["010001", "ONE MEDICAL CENTER", 6.9]
    assert without["hospital_distance_mi"] == "" and without["hospital_source"] == ""


def test_hospital_distance_is_a_scored_field_with_coverage_and_bounds() -> None:
    rows = [
        {
            **dict.fromkeys(build.CATALOG_COLUMNS, "1"),
            "population": "5000",
            "hospital_distance_mi": miles,
        }
        for miles in ("4.0", "9.0", "")
    ]

    coverage, bounds = build.coverage_and_bounds(rows)

    assert coverage["hospital_distance_mi"] == {"non_null": 2, "serving_places": 3, "share": 0.6667}
    assert bounds["hospital_distance_mi"] == {"lower": 4.0, "upper": 9.0}


def test_shipped_manifest_pins_the_cms_and_zcta_sources_and_counts_unlocated_facilities() -> None:
    manifest = load_catalog().manifest

    for key in ("cms_hospitals", "zcta_gazetteer"):
        assert len(manifest["inputs"][key]["sha256"]) == 64
        assert "Current" not in manifest["inputs"][key]["url"]
    counts = manifest["hospital_facilities"]
    assert counts["emergency_facilities"] == counts["located"] + counts["unlocated"]
    assert counts["located"] > 0.9 * counts["emergency_facilities"]
    assert "straight-line" in manifest["hospital_join"]
    assert "drive time is not estimated" in manifest["hospital_join"]


# -- Tier C: the quality never reaches a gate or an evidence-backed score ------------------


def test_no_gate_metric_weight_or_source_config_uses_the_hospital_distance_quality() -> None:
    for config in sorted((ROOT / "config").glob("*.yaml")):
        assert "hospital_distance" not in config.read_text("utf-8"), config.name


def test_evidence_seams_do_not_import_discovery() -> None:
    for name in ("gates", "scoring", "sensitivity", "evidence", "pipeline"):
        tree = ast.parse((ROOT / f"src/lifescape/{name}.py").read_text("utf-8"))
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        assert not any("discovery" in module for module in imported), name


def test_a_known_hospital_distance_cannot_pass_the_healthcare_gate(policy) -> None:
    real = load_catalog()
    traverse = real.lookup("Traverse City, MI")[0]
    assert traverse.values["hospital_distance_mi"] is not None
    gate = GateDefinition(
        id="healthcare", metric_id="er_drive_minutes", operator=GateOperator.MAX, threshold=25
    )

    results = evaluate_gates((traverse.place_id,), (), (gate,), policy)

    assert [item.result for item in results] == [GateState.UNKNOWN]
    assert results[0].raw_value is None


def test_a_hospital_component_cannot_become_an_observation() -> None:
    service = DiscoveryService(load_catalog())
    result = service.search(
        SearchProfile(targets={"hospital_distance_mi": 5, "population_density": 300})
    )
    item = result["recommendations"][0]
    component = next(c for c in item["components"] if c["field"] == "hospital_distance_mi")

    with pytest.raises(ValidationError):
        ObservationRecord.model_validate(component)
    assert item["evidence_status"] == "not verified evidence"


def test_recommendation_api_returns_the_hospital_facility_and_straight_line_label(
    tmp_path: Path,
) -> None:
    with TestClient(create_app(tmp_path / "output"), base_url="http://127.0.0.1") as client:
        bootstrap = client.get("/api/bootstrap").json()["discovery"]["fields"]
        response = client.post(
            "/api/place-recommendations",
            json={"targets": {"hospital_distance_mi": 5, "population_density": 300}},
            headers={"origin": "http://127.0.0.1"},
        )

    field = next(item for item in bootstrap if item["field"] == "hospital_distance_mi")
    assert field["label"] == "Nearest emergency hospital"
    assert field["unit"] == "straight-line miles"
    assert response.status_code == 200
    for item in response.json()["recommendations"]:
        source = item["hospital_source"]
        assert set(source) == {"facility", "name", "miles"} and source["name"]
        reason = next(r for r in item["reasons"] if r["field"] == "hospital_distance_mi")["text"]
        assert source["name"] in reason


# -- the catalog keeps the distance and its facility together -------------------------------

FIELD_META = {
    field: {"label": field.replace("_", " ").title(), "unit": "percent", "definition": field}
    for field in SCORED_FIELDS
}
FIELD_META["hospital_distance_mi"].update(
    label="Nearest emergency hospital", unit="straight-line miles"
)
BOUNDS = {field: {"lower": 0.0, "upper": 100.0} for field in SCORED_FIELDS}
BOUNDS["population"]["upper"] = 10000.0
BOUNDS["median_home_value"]["upper"] = 1000.0
FACILITY = ("010001", "SOUTHEAST HEALTH MEDICAL CENTER")
NO_QUALITIES = (None,) * 8  # climate and place-character qualities


def _source(miles: float) -> str:
    return json.dumps([*FACILITY, miles])


def _row(name: str, miles: float | None) -> tuple[str, str, float | None, ...]:
    return (name, "NC", 5000, 500, 50, 20, 40, 30, *NO_QUALITIES, miles)


def _catalog(
    rows: dict[str, tuple[str, str, float | None, ...]], sources: dict[str, str]
) -> PlaceCatalog:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CATALOG_COLUMNS)
    for place_id, (name, state, *values) in rows.items():
        writer.writerow(
            [place_id, name, state, STATE_REGIONS[state]]
            + ["" if value is None else value for value in values]
            + [""] * (len(SCORED_FIELDS) - len(values))
            + ["1.0", "0", "0", "", sources.get(place_id, "")]
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


def test_shipped_hospital_distance_names_its_facility_and_is_never_drive_time() -> None:
    real = load_catalog()
    meta = real.manifest["fields"]["hospital_distance_mi"]

    assert meta["unit"] == "straight-line miles"
    assert "not drive time" in meta["definition"].lower()
    assert real.manifest["coverage"]["hospital_distance_mi"]["share"] >= 0.8
    for place in real.places.values():
        value = place.values["hospital_distance_mi"]
        assert (value is None) == (place.hospital_source is None), place.place_id
        if place.hospital_source is not None:
            facility, name, miles = place.hospital_source
            assert facility and name and miles == value
    traverse = real.lookup("Traverse City, MI")[0]
    assert traverse.hospital_source is not None
    assert "MUNSON" in traverse.hospital_source[1].upper()
    assert traverse.hospital_source[2] < 10


def test_a_place_with_no_hospital_in_the_source_is_null_not_imputed() -> None:
    catalog = _catalog(
        {"NONE": _row("Remote", None), "NEAR": _row("Nearby", 8.3)}, {"NEAR": _source(8.3)}
    )

    assert catalog.places["NONE"].values["hospital_distance_mi"] is None
    assert catalog.places["NONE"].hospital_source is None
    assert catalog.places["NEAR"].hospital_source == (*FACILITY, 8.3)
    result = DiscoveryService(catalog).search(SearchProfile(exemplars=("NEAR",)))
    remote = next(item for item in result["recommendations"] if item["place_id"] == "NONE")
    assert "hospital_distance_mi" in remote["missing_fields"]
    assert remote["hospital_source"] is None
    assert all(c["field"] != "hospital_distance_mi" for c in remote["components"])


@pytest.mark.parametrize(
    ("source", "miles", "message"),
    [
        ("", 8.3, "without a facility"),
        (_source(8.3), None, "does not match"),
        (_source(8.3), 9.0, "does not match"),
        ('["010001","",8.3]', 8.3, "invalid hospital source"),
        ('["010001","Name",-1]', 8.3, "invalid hospital source"),
        ('["010001","Name",true]', 8.3, "invalid hospital source"),
        ('["010001","Name"]', 8.3, "invalid hospital source"),
        ('{"id":"x"}', 8.3, "invalid hospital source"),
        ("not json", 8.3, "could not be parsed"),
    ],
)
def test_catalog_rejects_a_hospital_distance_that_disagrees_with_its_facility(
    source: str, miles: float | None, message: str
) -> None:
    with pytest.raises(CatalogUnavailableError, match=message):
        _catalog({"A": _row("Alpha", miles)}, {"A": source} if source else {})


def test_straight_line_miles_keep_one_decimal_and_say_straight_line() -> None:
    assert format_value("straight-line miles", 0.84) == "0.8 straight-line miles"
    assert format_value("straight-line miles", 1234.0) == "1,234.0 straight-line miles"
    assert format_value("miles", 8.3) == "8 miles"


def test_match_card_hospital_reason_shows_the_facility_name_and_straight_line_miles() -> None:
    rows = {
        "EX": _row("Example", 10.0),
        # Alpha matches the example on size, price, and hospital distance, not on the rest.
        "A": ("Alpha", "NC", 5000, 500, 90, 80, 0, 100, *NO_QUALITIES, 8.3),
        "B": _row("Bravo", 60.0),
    }
    catalog = _catalog(rows, {"EX": _source(10.0), "A": _source(8.3), "B": _source(60.0)})
    result = DiscoveryService(catalog).search(SearchProfile(exemplars=("EX",)))
    alpha = next(item for item in result["recommendations"] if item["place_id"] == "A")

    reason = next(r for r in alpha["reasons"] if r["field"] == "hospital_distance_mi")["text"]
    assert reason == (
        "Nearest emergency hospital: 8.3 straight-line miles to SOUTHEAST HEALTH MEDICAL "
        "CENTER. Close to 10.0 straight-line miles in Example, NC."
    )
    assert alpha["hospital_source"] == {"facility": FACILITY[0], "name": FACILITY[1], "miles": 8.3}
