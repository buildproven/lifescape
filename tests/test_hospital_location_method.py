"""Hospital location method in place provenance (BUI-1491; PRD provenance goal, FR4, FR5).

A place's nearest-hospital provenance says whether the facility was located at its street
address by the Census Geocoder (``census_geocoder``) or at its ZIP Code Tabulation Area centroid
(``zcta_centroid``), so a user can tell how precise the distance is.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path

import pytest

from lifescape.discovery import (
    CATALOG_COLUMNS,
    HOSPITAL_LOCATION_METHODS,
    SCORED_FIELDS,
    STATE_REGIONS,
    CatalogUnavailableError,
    DiscoveryService,
    PlaceCatalog,
    SearchProfile,
    load_catalog,
    parse_catalog,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/build_place_catalog.py"
spec = importlib.util.spec_from_file_location("build_place_catalog_location_method", SCRIPT)
assert spec and spec.loader
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)

CMS = (
    b"Facility ID,Facility Name,State,ZIP Code,Emergency Services\n"
    b"010001,GEOCODED HOSPITAL,NC,28001,Yes\n"
    b"010002,CENTROID HOSPITAL,NC,28002,Yes\n"
)
CENTROIDS = {"28001": (35.0, -80.0), "28002": (36.0, -80.0)}


def _gazetteer_place(geoid: str, lat: str, lon: str) -> dict[str, str]:
    return {
        "GEOID": geoid,
        "USPS": "NC",
        "NAME": f"Town {geoid} town",
        "ALAND_SQMI": "10.0",
        "INTPTLAT": lat,
        "INTPTLONG": lon,
    }


# -- REQ-HOSP-1: the builder writes the facility's location method --------------------------


def test_req_hosp_1_located_hospitals_carry_their_location_method() -> None:
    hospitals, counts = build.read_cms_hospitals(CMS, CENTROIDS, {"010001": (35.1, -80.0)})

    assert hospitals == [
        ("010001", "GEOCODED HOSPITAL", 35.1, -80.0, "census_geocoder"),
        ("010002", "CENTROID HOSPITAL", 36.0, -80.0, "zcta_centroid"),
    ]
    assert counts["census_geocoder"] == 1 and counts["zcta_centroid"] == 1


def test_req_hosp_1_without_a_geocoder_response_every_hospital_is_a_zcta_centroid() -> None:
    hospitals, _ = build.read_cms_hospitals(CMS, CENTROIDS)

    assert [hospital[4] for hospital in hospitals] == ["zcta_centroid", "zcta_centroid"]


def test_req_hosp_1_built_rows_write_the_method_in_the_hospital_source() -> None:
    places = [
        _gazetteer_place("3700001", "35.0", "-80.0"),
        _gazetteer_place("3700002", "36.05", "-80.0"),
    ]
    hospitals, _ = build.read_cms_hospitals(CMS, CENTROIDS, {"010001": (35.1, -80.0)})

    near_geocoded, near_centroid = build.build_rows(
        places, {}, [], None, build.HospitalIndex(hospitals)
    )

    assert json.loads(near_geocoded["hospital_source"]) == [
        "010001",
        "GEOCODED HOSPITAL",
        6.9,
        "census_geocoder",
    ]
    assert json.loads(near_centroid["hospital_source"])[::3] == ["010002", "zcta_centroid"]


def test_req_hosp_1_the_shipped_catalog_records_a_method_for_every_hospital() -> None:
    catalog = load_catalog()
    counts = catalog.manifest["hospital_facilities"]
    methods = {
        place.hospital_source[3]
        for place in catalog.places.values()
        if place.hospital_source is not None
    }

    assert methods == {"census_geocoder", "zcta_centroid"}
    assert "location method" in catalog.manifest["hospital_join"]
    assert counts["census_geocoder"] > 0 and counts["zcta_centroid"] > 0


# -- REQ-HOSP-2: discovery rejects a method outside the two values ---------------------------

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
NO_QUALITIES = (None,) * 8  # climate and place-character qualities


def _catalog(sources: dict[str, str]) -> PlaceCatalog:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CATALOG_COLUMNS)
    for place_id, source in sources.items():
        values = (5000, 500, 50, 20, 40, 30, *NO_QUALITIES, 8.3)
        writer.writerow(
            [place_id, f"Town {place_id}", "NC", STATE_REGIONS["NC"], *values]
            + [""] * (len(SCORED_FIELDS) - len(values))
            + ["1.0", "0", "0", "", source]
        )
    compressed = gzip.compress(buffer.getvalue().encode(), mtime=0)
    manifest = {
        "catalog_version": "synthetic-fixture-v1",
        "data_date": "2000-01-01",
        "fields": FIELD_META,
        "bounds": BOUNDS,
        "row_count": len(sources),
        "output_sha256": hashlib.sha256(compressed).hexdigest(),
    }
    return parse_catalog(compressed, manifest)


def test_req_hosp_2_the_two_location_methods_are_the_only_ones() -> None:
    assert HOSPITAL_LOCATION_METHODS == ("census_geocoder", "zcta_centroid")


@pytest.mark.parametrize("method", ["census_geocoder", "zcta_centroid"])
def test_req_hosp_2_a_known_location_method_is_parsed(method: str) -> None:
    catalog = _catalog({"A": json.dumps(["010001", "ONE HOSPITAL", 8.3, method])})

    assert catalog.places["A"].hospital_source == ("010001", "ONE HOSPITAL", 8.3, method)


@pytest.mark.parametrize(
    "source",
    [
        '["010001","ONE HOSPITAL",8.3]',  # legacy row without a method
        '["010001","ONE HOSPITAL",8.3,"street_address"]',
        '["010001","ONE HOSPITAL",8.3,""]',
        '["010001","ONE HOSPITAL",8.3,"CENSUS_GEOCODER"]',
        '["010001","ONE HOSPITAL",8.3,null]',
        '["010001","ONE HOSPITAL",8.3,1]',
        '["010001","ONE HOSPITAL",8.3,["zcta_centroid"]]',
        '["010001","ONE HOSPITAL",8.3,"zcta_centroid","extra"]',
    ],
    ids=["missing", "unknown", "blank", "case", "null", "number", "list", "extra"],
)
def test_req_hosp_2_a_missing_or_unknown_location_method_rejects_the_catalog(
    source: str,
) -> None:
    with pytest.raises(CatalogUnavailableError, match="invalid hospital source"):
        _catalog({"A": source})


# -- REQ-HOSP-3: the result JSON carries the location method ---------------------------------


def test_req_hosp_3_a_discovery_result_shows_the_hospital_location_method() -> None:
    catalog = _catalog(
        {
            "A": json.dumps(["010001", "ONE HOSPITAL", 8.3, "zcta_centroid"]),
            "B": json.dumps(["010002", "TWO HOSPITAL", 8.3, "census_geocoder"]),
        }
    )

    result = DiscoveryService(catalog).search(SearchProfile(exemplars=("A",)))
    sources = {item["place_id"]: item["hospital_source"] for item in result["recommendations"]}

    assert sources["B"] == {
        "facility": "010002",
        "name": "TWO HOSPITAL",
        "miles": 8.3,
        "location_method": "census_geocoder",
    }
    json.dumps(result)  # the result stays JSON-serializable


def test_req_hosp_3_a_shipped_catalog_place_exposes_its_location_method() -> None:
    catalog = load_catalog()
    traverse = catalog.lookup("Traverse City, MI")[0]

    result = DiscoveryService(catalog).search(SearchProfile(exemplars=(traverse.place_id,)))

    for item in result["recommendations"]:
        source = item["hospital_source"]
        if source is not None:
            assert source["location_method"] in HOSPITAL_LOCATION_METHODS
