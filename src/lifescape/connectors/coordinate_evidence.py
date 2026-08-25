"""Provenance-bearing coordinate evidence for future ER route calculations.

This module prepares route endpoints. It does not calculate a route or emit an
``er_drive_minutes`` observation. A caller must supply versioned Census and CMS
sources; moving ``Current`` aliases are rejected because they are not reproducible.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from datetime import date
from enum import StrEnum
from typing import Final
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import urlopen

from pydantic import Field, model_validator

from lifescape.connectors.base import RawResponse
from lifescape.models import StrictModel

CMS_DATASET_ID: Final = "xubh-q36u"
CMS_DATASET_PAGE: Final = f"https://data.cms.gov/provider-data/dataset/{CMS_DATASET_ID}"
CENSUS_GEOCODER_URL: Final = "https://geocoding.geo.census.gov/geocoder/locations/address"
REQUEST_TIMEOUT_SECONDS: Final = 20
SHA256_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")


class CoordinateEvidenceError(ValueError):
    """Raised when coordinate source data is malformed or not reproducible."""


class CoordinateSemantics(StrEnum):
    CENSUS_PLACE_INTERNAL_POINT = "census_place_internal_point"
    GEOCODED_STRUCTURE_ADDRESS = "geocoded_structure_address"


class GeocoderMatchQuality(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    SINGLE_MATCH = "single_match"


class CoordinateEvidence(StrictModel):
    """One coordinate with the evidence required to reproduce its meaning."""

    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    semantics: CoordinateSemantics
    source_url: str
    source_title: str
    publisher: str
    source_version: str
    retrieved_at: date
    response_checksum: str
    original_address: str | None = None
    matched_address: str | None = None
    match_quality: GeocoderMatchQuality = GeocoderMatchQuality.NOT_APPLICABLE

    @model_validator(mode="after")
    def provenance_matches_semantics(self) -> CoordinateEvidence:
        if not self.source_title.strip() or self.publisher != "U.S. Census Bureau":
            raise ValueError("coordinate evidence requires an identified U.S. Census source")
        _require_government_host(self.source_url, "census.gov", "coordinate evidence")
        if not SHA256_PATTERN.fullmatch(self.response_checksum):
            raise ValueError("response_checksum must be a lowercase SHA-256 digest")
        if not self.source_version.strip() or "current" in self.source_version.casefold():
            raise ValueError("source_version must be explicit and cannot use a Current alias")
        address_fields = (self.original_address, self.matched_address)
        if self.semantics is CoordinateSemantics.GEOCODED_STRUCTURE_ADDRESS:
            if not all(value and value.strip() for value in address_fields):
                raise ValueError("geocoded coordinates require original and matched addresses")
            if self.match_quality is not GeocoderMatchQuality.SINGLE_MATCH:
                raise ValueError("geocoded coordinates require one unambiguous address match")
        elif any(address_fields) or self.match_quality is not GeocoderMatchQuality.NOT_APPLICABLE:
            raise ValueError("place internal points cannot contain address-match fields")
        return self


class HospitalAddress(StrictModel):
    street: str
    city: str
    state: str = Field(min_length=2, max_length=2)
    zip_code: str

    @model_validator(mode="after")
    def required_parts_are_present(self) -> HospitalAddress:
        if not all(value.strip() for value in (self.street, self.city, self.state, self.zip_code)):
            raise ValueError("hospital address fields cannot be blank")
        if not re.fullmatch(r"\d{5}(?:-\d{4})?", self.zip_code):
            raise ValueError("hospital ZIP code must contain five or nine digits")
        return self

    @property
    def full_address(self) -> str:
        return f"{self.street}, {self.city}, {self.state} {self.zip_code}"


class CmsHospital(StrictModel):
    facility_id: str
    facility_name: str
    address: HospitalAddress


class CmsHospitalSnapshot(StrictModel):
    """Emergency-capable CMS hospitals retained from one immutable response."""

    dataset_id: str
    dataset_version: str
    source_url: str
    retrieved_at: date
    response_checksum: str
    hospitals: tuple[CmsHospital, ...]


class RouteEndpoints(StrictModel):
    """Validated endpoints that a later routing provider may consume."""

    origin: CoordinateEvidence
    destination: CoordinateEvidence
    destination_facility_id: str

    @model_validator(mode="after")
    def endpoint_semantics_are_explicit(self) -> RouteEndpoints:
        if self.origin.semantics is not CoordinateSemantics.CENSUS_PLACE_INTERNAL_POINT:
            raise ValueError("route origin must be a Census place internal point")
        if self.destination.semantics is not CoordinateSemantics.GEOCODED_STRUCTURE_ADDRESS:
            raise ValueError("route destination must be a geocoded structure address")
        if not self.destination_facility_id.strip():
            raise ValueError("destination_facility_id cannot be blank")
        return self


def fetch_snapshot(source_url: str) -> RawResponse:
    """Fetch an operator-selected, versioned source without adding hidden fallback."""
    if not source_url.startswith("https://"):
        raise CoordinateEvidenceError("coordinate source URL must use HTTPS")
    try:
        with urlopen(source_url, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            payload = response.read()
    except (HTTPError, URLError) as exc:
        raise CoordinateEvidenceError(f"coordinate source request failed: {exc}") from exc
    return RawResponse(
        source_url=source_url,
        payload=payload,
        checksum=hashlib.sha256(payload).hexdigest(),
    )


def parse_cms_emergency_hospitals(
    response: RawResponse,
    *,
    dataset_version: str,
    retrieved_at: date,
) -> CmsHospitalSnapshot:
    """Parse one CMS CSV snapshot and keep only explicit emergency-service rows."""
    _require_version(dataset_version, "CMS dataset_version")
    _require_government_host(response.source_url, "cms.gov", "CMS hospital snapshot")
    _require_checksum(response)
    try:
        text = response.payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CoordinateEvidenceError("CMS hospital snapshot must be UTF-8 CSV") from exc
    reader = csv.DictReader(io.StringIO(text))
    required = {
        "Facility ID",
        "Facility Name",
        "Address",
        "City/Town",
        "State",
        "ZIP Code",
        "Emergency Services",
    }
    missing = sorted(required - set(reader.fieldnames or ()))
    if missing:
        raise CoordinateEvidenceError(f"CMS hospital snapshot is missing columns: {missing}")

    hospitals: list[CmsHospital] = []
    seen: set[str] = set()
    for line_number, row in enumerate(reader, start=2):
        if (row.get("Emergency Services") or "").strip().casefold() != "yes":
            continue
        try:
            hospital = CmsHospital(
                facility_id=(row.get("Facility ID") or "").strip(),
                facility_name=(row.get("Facility Name") or "").strip(),
                address=HospitalAddress(
                    street=(row.get("Address") or "").strip(),
                    city=(row.get("City/Town") or "").strip(),
                    state=(row.get("State") or "").strip(),
                    zip_code=(row.get("ZIP Code") or "").strip(),
                ),
            )
        except ValueError as exc:
            raise CoordinateEvidenceError(
                f"invalid emergency-capable CMS hospital on CSV line {line_number}: {exc}"
            ) from exc
        if not hospital.facility_id or not hospital.facility_name:
            raise CoordinateEvidenceError(
                f"emergency-capable CMS hospital on CSV line {line_number} lacks identity"
            )
        if hospital.facility_id in seen:
            raise CoordinateEvidenceError(
                f"duplicate CMS facility ID in snapshot: {hospital.facility_id}"
            )
        seen.add(hospital.facility_id)
        hospitals.append(hospital)

    return CmsHospitalSnapshot(
        dataset_id=CMS_DATASET_ID,
        dataset_version=dataset_version,
        source_url=response.source_url,
        retrieved_at=retrieved_at,
        response_checksum=response.checksum,
        hospitals=tuple(sorted(hospitals, key=lambda hospital: hospital.facility_id)),
    )


def parse_census_place_internal_point(
    response: RawResponse,
    *,
    gazetteer_version: str,
    state_fips: str,
    place_fips: str,
    retrieved_at: date,
) -> CoordinateEvidence | None:
    """Return the one matching versioned Gazetteer place internal point, if present."""
    _require_version(gazetteer_version, "Census Gazetteer version")
    _require_government_host(response.source_url, "census.gov", "Census Gazetteer")
    _require_checksum(response)
    if not re.fullmatch(r"\d{2}", state_fips) or not re.fullmatch(r"\d{5}", place_fips):
        raise CoordinateEvidenceError("state_fips and place_fips must contain two and five digits")
    try:
        text = response.payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CoordinateEvidenceError("Census Gazetteer snapshot must be UTF-8 text") from exc
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    required = {"GEOID", "NAME", "INTPTLAT", "INTPTLONG"}
    missing = sorted(required - set(reader.fieldnames or ()))
    if missing:
        raise CoordinateEvidenceError(f"Census Gazetteer is missing columns: {missing}")
    geoid = f"{state_fips}{place_fips}"
    matches = [row for row in reader if (row.get("GEOID") or "").strip() == geoid]
    if not matches:
        return None
    if len(matches) != 1:
        raise CoordinateEvidenceError(f"Census Gazetteer contains duplicate GEOID {geoid}")
    row = matches[0]
    try:
        latitude = float((row.get("INTPTLAT") or "").strip())
        longitude = float((row.get("INTPTLONG") or "").strip())
    except ValueError as exc:
        raise CoordinateEvidenceError(f"Census Gazetteer GEOID {geoid} lacks coordinates") from exc
    return CoordinateEvidence(
        latitude=latitude,
        longitude=longitude,
        semantics=CoordinateSemantics.CENSUS_PLACE_INTERNAL_POINT,
        source_url=response.source_url,
        source_title=(
            f"U.S. Census Gazetteer {gazetteer_version}: {(row.get('NAME') or '').strip()}"
        ),
        publisher="U.S. Census Bureau",
        source_version=gazetteer_version,
        retrieved_at=retrieved_at,
        response_checksum=response.checksum,
    )


class CensusAddressGeocoder:
    """Geocode CMS hospital addresses against one pinned Census benchmark."""

    def __init__(self, *, benchmark: str, retrieved_at: date) -> None:
        _require_version(benchmark, "Census geocoder benchmark")
        self._benchmark = benchmark
        self._retrieved_at = retrieved_at

    def geocode(self, hospital: CmsHospital) -> CoordinateEvidence | None:
        query = urlencode(
            {
                "street": hospital.address.street,
                "city": hospital.address.city,
                "state": hospital.address.state,
                "zip": hospital.address.zip_code,
                "benchmark": self._benchmark,
                "format": "json",
            }
        )
        response = fetch_snapshot(f"{CENSUS_GEOCODER_URL}?{query}")
        return self.normalize(response, hospital=hospital)

    def normalize(
        self, response: RawResponse, *, hospital: CmsHospital
    ) -> CoordinateEvidence | None:
        _require_government_host(response.source_url, "census.gov", "Census geocoder response")
        _require_checksum(response)
        try:
            decoded = json.loads(response.payload)
            result = decoded["result"]
            returned_benchmark = result["input"]["benchmark"]["benchmarkName"]
            matches = result["addressMatches"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise CoordinateEvidenceError("Census geocoder returned an invalid response") from exc
        if returned_benchmark != self._benchmark:
            raise CoordinateEvidenceError(
                "Census geocoder response benchmark does not match the requested benchmark"
            )
        if not isinstance(matches, list):
            raise CoordinateEvidenceError("Census geocoder addressMatches must be a list")
        if len(matches) != 1:
            return None
        match = matches[0]
        try:
            longitude = float(match["coordinates"]["x"])
            latitude = float(match["coordinates"]["y"])
            matched_address = str(match["matchedAddress"]).strip()
        except (KeyError, TypeError, ValueError) as exc:
            raise CoordinateEvidenceError("Census geocoder match lacks valid coordinates") from exc
        if not matched_address:
            return None
        return CoordinateEvidence(
            latitude=latitude,
            longitude=longitude,
            semantics=CoordinateSemantics.GEOCODED_STRUCTURE_ADDRESS,
            source_url=response.source_url,
            source_title="U.S. Census Geocoder address match",
            publisher="U.S. Census Bureau",
            source_version=self._benchmark,
            retrieved_at=self._retrieved_at,
            response_checksum=response.checksum,
            original_address=hospital.address.full_address,
            matched_address=matched_address,
            match_quality=GeocoderMatchQuality.SINGLE_MATCH,
        )


def build_route_endpoints(
    *,
    origin: CoordinateEvidence | None,
    destination: CoordinateEvidence | None,
    destination_facility_id: str,
) -> RouteEndpoints | None:
    """Return complete route inputs or visible missing evidence; never estimate either end."""
    if origin is None or destination is None:
        return None
    return RouteEndpoints(
        origin=origin,
        destination=destination,
        destination_facility_id=destination_facility_id,
    )


def _require_checksum(response: RawResponse) -> None:
    actual = hashlib.sha256(response.payload).hexdigest()
    if response.checksum != actual:
        raise CoordinateEvidenceError("source response checksum does not match its payload")


def _require_version(version: str, label: str) -> None:
    if not version.strip() or "current" in version.casefold():
        raise CoordinateEvidenceError(f"{label} must be explicit and cannot use a Current alias")


def _require_government_host(source_url: str, domain: str, label: str) -> None:
    parsed = urlparse(source_url)
    hostname = (parsed.hostname or "").casefold()
    if parsed.scheme != "https" or not (hostname == domain or hostname.endswith(f".{domain}")):
        raise CoordinateEvidenceError(f"{label} must come from an HTTPS {domain} source")
