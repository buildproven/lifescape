"""Census Geocoder hospital locations (BUI-1444 R1; PRD FR4, FR5).

R1: CMS emergency hospitals are located by their street address with the pinned
``Public_AR_Census2020`` Census Geocoder benchmark. The ZIP Code Tabulation Area centroid is
only a fallback for a facility the geocoder does not match, so a PO Box ZIP Code no longer drops
a facility the geocoder can place.
"""

from __future__ import annotations

import csv
import importlib.util
import io
from email.parser import BytesParser
from email.policy import HTTP
from pathlib import Path

import pytest

from lifescape.discovery import load_catalog

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/build_place_catalog.py"
spec = importlib.util.spec_from_file_location("build_place_catalog_geocoding", SCRIPT)
assert spec and spec.loader
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)

HEADER = "Facility ID,Facility Name,Address,City/Town,State,ZIP Code,Emergency Services\n"
CENTROIDS = {"36301": (31.2, -85.4), "28001": (35.0, -80.0)}


def _cms(*rows: str, header: str = HEADER) -> bytes:
    return (header + "".join(row + "\n" for row in rows)).encode()


CMS = _cms(
    "010001,SOUTHEAST HEALTH MEDICAL CENTER,1108 ROSS CLARK CIRCLE,DOTHAN,AL,36301,Yes",
    "010055,FLOWERS HOSPITAL,4370 WEST MAIN STREET,DOTHAN,AL,36302,Yes",
    '340001,"ONE, MEDICAL CENTER","10 MAIN ST, SUITE 2",TOWN,NC,28001,Yes',
    "340002,NO ER CLINIC,1 ELM ST,TOWN,NC,28001,No",
    "400001,PUERTO RICO HOSPITAL,1 CALLE,SAN JUAN,PR,00901,Yes",
    "010099,PO BOX ONLY HOSPITAL,PO BOX 9,DOTHAN,AL,36302,Yes",
)
MATCH = '"{id}","{address}","Match","Exact","{address}","{lon},{lat}","1","L"'


def _response(*rows: str) -> bytes:
    return "".join(row + "\n" for row in rows).encode()


GEOCODED = _response(
    MATCH.format(
        id="010055", address="4370 W MAIN ST, DOTHAN, AL, 36305", lon=-85.4577, lat=31.236
    ),
    MATCH.format(
        id="010001", address="1108 ROSS CLARK CIR, DOTHAN, AL, 36301", lon=-85.36, lat=31.2
    ),
    '"340001","10 MAIN ST, SUITE 2, TOWN, NC, 28001","No_Match"',
    '"010099","PO BOX 9, DOTHAN, AL, 36302","Tie"',
)
IDS = ("010001", "010055", "010099", "340001")


def test_r1_geocoder_request_lists_emergency_catalog_state_facilities_by_street_address() -> None:
    assert build.geocoder_addresses(CMS) == [
        ["010001", "1108 ROSS CLARK CIRCLE", "DOTHAN", "AL", "36301"],
        ["010055", "4370 WEST MAIN STREET", "DOTHAN", "AL", "36302"],
        ["010099", "PO BOX 9", "DOTHAN", "AL", "36302"],
        ["340001", "10 MAIN ST, SUITE 2", "TOWN", "NC", "28001"],
    ]
    with pytest.raises(SystemExit, match="missing columns"):
        build.geocoder_addresses(
            _cms("010001,A,AL,36301,Yes", header=",".join(build.HOSPITAL_COLUMNS) + "\n")
        )


def test_r1_geocoder_matches_become_latitude_longitude_and_misses_stay_unmatched() -> None:
    assert build.read_geocoder_matches(GEOCODED, IDS) == {
        "010001": (31.2, -85.36),
        "010055": (31.236, -85.4577),
    }


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (GEOCODED + b'"999999","X","No_Match"\n', "unrequested facility 999999"),
        (GEOCODED + b'"010001","X","No_Match"\n', "repeats facility 010001"),
        (_response(*GEOCODED.decode().splitlines()[:3]), r"omits facilities \['010099'\]"),
        (GEOCODED.replace(b'"Tie"', b'"Maybe"'), "unknown match status"),
        (GEOCODED.replace(b"-85.36,31.2", b"-85.36"), "unreadable coordinates"),
        (GEOCODED.replace(b"-85.36,31.2", b"31.2,-185.36"), "unreadable coordinates"),
        (GEOCODED.replace(b"-85.36,31.2", b"nan,31.2"), "unreadable coordinates"),
    ],
    ids=["unrequested", "repeated", "omitted", "status", "one-coordinate", "range", "nan"],
)
def test_r1_an_incomplete_or_malformed_geocoder_response_stops_the_build(
    response: bytes, message: str
) -> None:
    with pytest.raises(SystemExit, match=message):
        build.read_geocoder_matches(response, IDS)


def test_r1_geocoded_address_wins_and_the_zcta_centroid_is_only_a_fallback() -> None:
    geocoded = build.read_geocoder_matches(GEOCODED, IDS)

    hospitals, counts = build.read_cms_hospitals(CMS, CENTROIDS, geocoded)

    assert hospitals == [
        ("010001", "SOUTHEAST HEALTH MEDICAL CENTER", 31.2, -85.36),  # address, not centroid
        ("010055", "FLOWERS HOSPITAL", 31.236, -85.4577),  # PO Box ZIP 36302 is not a ZCTA
        ("340001", "ONE, MEDICAL CENTER", 35.0, -80.0),  # unmatched: ZCTA centroid fallback
    ]
    assert counts == {
        "emergency_facilities": 4,
        "located": 3,
        "unlocated": 1,
        "census_geocoder": 2,
        "zcta_centroid": 1,
    }


def test_r1_geocoding_posts_pinned_benchmark_batches_and_caches_a_sorted_response(
    tmp_path: Path,
) -> None:
    addresses = [[f"{n:06d}", f"{n} MAIN ST", "TOWN", "NC", "28001"] for n in range(10_001)]
    posted: list[list[list[str]]] = []

    def post(request: bytes) -> bytes:
        batch = list(csv.reader(io.StringIO(request.decode())))
        posted.append(batch)
        return "".join(f'"{row[0]}","{row[1]}","No_Match"\n' for row in reversed(batch)).encode()

    first = build.census_geocode(addresses, tmp_path, post)
    again = build.census_geocode(addresses, tmp_path, post)

    assert [len(batch) for batch in posted] == [build.GEOCODER_BATCH_LIMIT, 1]
    assert first == again
    rows = list(csv.reader(io.StringIO(first.decode())))
    assert [row[0] for row in rows] == [address[0] for address in addresses]
    (cached,) = tmp_path.iterdir()
    assert "Public_AR_Census2020" in cached.name
    build.census_geocode(addresses[:1], tmp_path, post)
    assert len(posted) == 3  # a different request is a different cache entry


def test_r1_geocoder_request_is_a_multipart_upload_to_the_pinned_benchmark() -> None:
    request = build.geocoder_request(b"010001,1 MAIN ST,TOWN,NC,28001\n")

    assert request.full_url == "https://geocoding.geo.census.gov/geocoder/locations/addressbatch"
    assert "Current" not in request.full_url
    content_type = request.get_header("Content-type")
    assert content_type is not None and content_type.startswith("multipart/form-data")
    boundary = content_type.split("boundary=", 1)[1]
    assert 1 <= len(boundary) <= 70  # RFC 2046; the geocoder firewall rejects a longer one
    assert isinstance(request.data, bytes)
    message = BytesParser(policy=HTTP).parsebytes(
        f"Content-Type: {content_type}\r\n\r\n".encode() + request.data
    )
    parts = {
        part.get_param("name", header="content-disposition"): part for part in message.iter_parts()
    }
    assert parts["benchmark"].get_content().strip() == "Public_AR_Census2020"
    assert parts["addressFile"].get_filename() == "addresses.csv"
    assert parts["addressFile"].get_payload(decode=True) == b"010001,1 MAIN ST,TOWN,NC,28001\n"


def test_r1_shipped_catalog_locates_hospitals_with_the_pinned_census_geocoder() -> None:
    catalog = load_catalog()
    manifest = catalog.manifest

    geocoder = manifest["inputs"]["census_geocoder"]
    assert len(geocoder["sha256"]) == 64
    assert "Public_AR_Census2020" in geocoder["url"] and "Current" not in geocoder["url"]
    counts = manifest["hospital_facilities"]
    assert counts["located"] == counts["census_geocoder"] + counts["zcta_centroid"]
    assert counts["census_geocoder"] > 0.8 * counts["located"]
    assert counts["unlocated"] < 103  # the ZCTA-only build dropped 103 facilities
    assert "Census Geocoder" in manifest["hospital_join"]
    assert "Census Geocoder" in manifest["fields"]["hospital_distance_mi"]["definition"]
    facilities = {
        place.hospital_source[0]
        for place in catalog.places.values()
        if place.hospital_source is not None
    }
    assert "010055" in facilities  # FLOWERS HOSPITAL, Dothan AL, PO Box ZIP Code 36302


def test_r1_a_rejected_geocoder_response_is_not_cached(tmp_path: Path) -> None:
    addresses = [["010001", "1 MAIN ST", "TOWN", "NC", "28001"]]

    with pytest.raises(SystemExit, match="unrequested facility"):
        build.census_geocode(addresses, tmp_path, lambda _: b"<html>Service Unavailable</html>\n")

    assert list(tmp_path.iterdir()) == []
