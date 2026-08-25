from __future__ import annotations

import hashlib
import json
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from lifescape.connectors.base import RawResponse
from lifescape.connectors.coordinate_evidence import (
    CensusAddressGeocoder,
    CoordinateEvidence,
    CoordinateEvidenceError,
    CoordinateSemantics,
    GeocoderMatchQuality,
    build_route_endpoints,
    fetch_snapshot,
    parse_census_place_internal_point,
    parse_cms_emergency_hospitals,
)


def _response(payload: bytes, url: str = "https://data.cms.gov/versioned-snapshot") -> RawResponse:
    return RawResponse(
        source_url=url,
        payload=payload,
        checksum=hashlib.sha256(payload).hexdigest(),
    )


def _cms_csv(*rows: str) -> bytes:
    header = "Facility ID,Facility Name,Address,City/Town,State,ZIP Code,Emergency Services\n"
    return (header + "\n".join(rows) + "\n").encode()


def test_cms_snapshot_keeps_only_emergency_hospitals_with_provenance() -> None:
    response = _response(
        _cms_csv(
            "100001,Emergency Hospital,1 Main St,Madison,WI,53703,Yes",
            "100002,Clinic,2 Main St,Madison,WI,53703,No",
        ),
        "https://data.cms.gov/versioned-hospital.csv",
    )

    snapshot = parse_cms_emergency_hospitals(
        response, dataset_version="2026-08-13", retrieved_at=date(2026, 8, 24)
    )

    assert snapshot.dataset_id == "xubh-q36u"
    assert snapshot.dataset_version == "2026-08-13"
    assert snapshot.response_checksum == response.checksum
    assert [hospital.facility_id for hospital in snapshot.hospitals] == ["100001"]
    assert snapshot.hospitals[0].address.full_address == "1 Main St, Madison, WI 53703"


@pytest.mark.parametrize(
    "payload,match",
    [
        (_cms_csv("100001,Hospital,,Madison,WI,53703,Yes"), "cannot be blank"),
        (_cms_csv("100001,Hospital,1 Main St,Madison,WI,invalid,Yes"), "ZIP code"),
        (
            _cms_csv(
                "100001,Hospital,1 Main St,Madison,WI,53703,Yes",
                "100001,Other,2 Main St,Madison,WI,53703,Yes",
            ),
            "duplicate CMS facility ID",
        ),
    ],
)
def test_cms_snapshot_rejects_malformed_or_ambiguous_rows(payload: bytes, match: str) -> None:
    with pytest.raises(CoordinateEvidenceError, match=match):
        parse_cms_emergency_hospitals(
            _response(payload), dataset_version="2026-08-13", retrieved_at=date(2026, 8, 24)
        )


def test_cms_snapshot_rejects_moving_version_alias_and_bad_checksum() -> None:
    response = _response(_cms_csv("100001,Hospital,1 Main St,Madison,WI,53703,Yes"))
    with pytest.raises(CoordinateEvidenceError, match="Current alias"):
        parse_cms_emergency_hospitals(
            response, dataset_version="current", retrieved_at=date(2026, 8, 24)
        )
    with pytest.raises(CoordinateEvidenceError, match="checksum"):
        parse_cms_emergency_hospitals(
            response.model_copy(update={"checksum": "0" * 64}),
            dataset_version="2026-08-13",
            retrieved_at=date(2026, 8, 24),
        )


def test_gazetteer_origin_preserves_internal_point_semantics() -> None:
    payload = (
        b"USPS\tGEOID\tNAME\tINTPTLAT\tINTPTLONG\n"
        b"WI\t5543075\tLake Geneva city\t+42.5916835\t-088.4334301\n"
    )
    origin = parse_census_place_internal_point(
        _response(
            payload,
            "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2025_Gaz_place_55.txt",
        ),
        gazetteer_version="2025",
        state_fips="55",
        place_fips="43075",
        retrieved_at=date(2026, 8, 24),
    )

    assert origin is not None
    assert origin.latitude == pytest.approx(42.5916835)
    assert origin.longitude == pytest.approx(-88.4334301)
    assert origin.semantics is CoordinateSemantics.CENSUS_PLACE_INTERNAL_POINT
    assert origin.source_version == "2025"
    assert origin.original_address is None


def test_gazetteer_missing_place_remains_missing_evidence() -> None:
    payload = b"GEOID\tNAME\tINTPTLAT\tINTPTLONG\n"
    assert (
        parse_census_place_internal_point(
            _response(payload, "https://www2.census.gov/versioned-gazetteer.txt"),
            gazetteer_version="2025",
            state_fips="55",
            place_fips="43075",
            retrieved_at=date(2026, 8, 24),
        )
        is None
    )


def _hospital():
    snapshot = parse_cms_emergency_hospitals(
        _response(_cms_csv("100001,Hospital,1 Main St,Madison,WI,53703,Yes")),
        dataset_version="2026-08-13",
        retrieved_at=date(2026, 8, 24),
    )
    return snapshot.hospitals[0]


def _geocoder_response(matches: list[dict[str, object]], benchmark: str = "Public_AR_ACS2024"):
    payload = json.dumps(
        {
            "result": {
                "input": {"benchmark": {"benchmarkName": benchmark}},
                "addressMatches": matches,
            }
        }
    ).encode()
    return _response(payload, "https://geocoding.geo.census.gov/pinned-request")


def test_geocoder_preserves_address_match_and_response_provenance() -> None:
    geocoder = CensusAddressGeocoder(benchmark="Public_AR_ACS2024", retrieved_at=date(2026, 8, 24))
    destination = geocoder.normalize(
        _geocoder_response(
            [
                {
                    "coordinates": {"x": -89.384, "y": 43.074},
                    "matchedAddress": "1 MAIN ST, MADISON, WI, 53703",
                }
            ]
        ),
        hospital=_hospital(),
    )

    assert destination is not None
    assert destination.semantics is CoordinateSemantics.GEOCODED_STRUCTURE_ADDRESS
    assert destination.original_address == "1 Main St, Madison, WI 53703"
    assert destination.matched_address == "1 MAIN ST, MADISON, WI, 53703"
    assert destination.facility_id == "100001"
    assert destination.match_quality is GeocoderMatchQuality.SINGLE_MATCH
    assert (
        destination.response_checksum
        == hashlib.sha256(
            _geocoder_response(
                [
                    {
                        "coordinates": {"x": -89.384, "y": 43.074},
                        "matchedAddress": "1 MAIN ST, MADISON, WI, 53703",
                    }
                ]
            ).payload
        ).hexdigest()
    )


@pytest.mark.parametrize("matches", [[], [{}, {}]])
def test_geocoder_missing_or_ambiguous_match_remains_missing(matches) -> None:
    geocoder = CensusAddressGeocoder(benchmark="Public_AR_ACS2024", retrieved_at=date(2026, 8, 24))
    assert geocoder.normalize(_geocoder_response(matches), hospital=_hospital()) is None


def test_geocoder_rejects_moving_alias_and_response_benchmark_mismatch() -> None:
    with pytest.raises(CoordinateEvidenceError, match="Current alias"):
        CensusAddressGeocoder(benchmark="Public_AR_Current", retrieved_at=date(2026, 8, 24))
    geocoder = CensusAddressGeocoder(benchmark="Public_AR_ACS2024", retrieved_at=date(2026, 8, 24))
    with pytest.raises(CoordinateEvidenceError, match="does not match"):
        geocoder.normalize(
            _geocoder_response([], benchmark="Public_AR_ACS2023"), hospital=_hospital()
        )


def _coordinate(semantics: CoordinateSemantics) -> CoordinateEvidence:
    address_fields = (
        {
            "original_address": "1 Main St, Madison, WI 53703",
            "matched_address": "1 MAIN ST, MADISON, WI, 53703",
            "facility_id": "100001",
            "match_quality": GeocoderMatchQuality.SINGLE_MATCH,
        }
        if semantics is CoordinateSemantics.GEOCODED_STRUCTURE_ADDRESS
        else {}
    )
    return CoordinateEvidence(
        latitude=43.0,
        longitude=-89.0,
        semantics=semantics,
        source_url="https://www2.census.gov/versioned-source",
        source_title="Source",
        publisher="U.S. Census Bureau",
        source_version="2025",
        retrieved_at=date(2026, 8, 24),
        response_checksum="a" * 64,
        **address_fields,
    )


def test_route_endpoints_require_both_provenance_bearing_coordinates() -> None:
    origin = _coordinate(CoordinateSemantics.CENSUS_PLACE_INTERNAL_POINT)
    destination = _coordinate(CoordinateSemantics.GEOCODED_STRUCTURE_ADDRESS)
    endpoints = build_route_endpoints(
        origin=origin, destination=destination, destination_facility_id="100001"
    )
    assert endpoints is not None
    assert endpoints.origin is origin
    assert endpoints.destination is destination
    with pytest.raises(ValueError, match="does not match coordinate evidence"):
        build_route_endpoints(
            origin=origin, destination=destination, destination_facility_id="different"
        )
    assert (
        build_route_endpoints(
            origin=None, destination=destination, destination_facility_id="100001"
        )
        is None
    )
    assert (
        build_route_endpoints(origin=origin, destination=None, destination_facility_id="100001")
        is None
    )


def test_fetch_snapshot_records_redirect_target_for_source_validation() -> None:
    handle = MagicMock()
    handle.read.return_value = b"payload"
    handle.geturl.return_value = "https://untrusted.example/redirected-payload"
    handle.__enter__.return_value = handle
    handle.__exit__.return_value = False
    with patch("lifescape.connectors.coordinate_evidence.urlopen", return_value=handle):
        response = fetch_snapshot("https://data.cms.gov/requested-source")

    assert response.source_url == "https://untrusted.example/redirected-payload"
    with pytest.raises(CoordinateEvidenceError, match=r"HTTPS cms\.gov"):
        parse_cms_emergency_hospitals(
            response, dataset_version="2026-08-13", retrieved_at=date(2026, 8, 24)
        )
