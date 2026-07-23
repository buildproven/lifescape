"""NOAA NCEI GSOY connector for station-explicit annual snowfall evidence.

The connector intentionally does not discover a "nearest" station or combine
stations/years.  A caller must choose one NCEI station and one completed
calendar year, encoded as ``<station_id>:<year>`` (for example,
``USC00218450:2024``).  GSOY's ``SNOW`` field is the direct total snowfall for
that station-year; it is requested in NOAA's standard (inch) units.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Final
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import urlopen

from lifescape.connectors.base import DataRequest, RawResponse, ValidationResult
from lifescape.models import Confidence, ObservationRecord, PlaceRecord, SourceRecord, SourceTier

NCEI_DATA_SERVICE_URL: Final = "https://www.ncei.noaa.gov/access/services/data/v1"
GSOY_DATASET: Final = "global-summary-of-the-year"
SNOWFALL_METRIC_ID: Final = "annual_snowfall"
REQUEST_TIMEOUT_SECONDS: Final = 10


class NoaaGsoyError(ValueError):
    """Raised when the NCEI GSOY service cannot provide usable snowfall evidence."""


class NoaaGsoyConnector:
    """Fetch a direct NCEI GSOY total annual snowfall for an explicit station-year."""

    name = "noaa_gsoy"
    supported_metric_ids: tuple[str, ...] = (SNOWFALL_METRIC_ID,)

    def __init__(self, *, retrieved_at: date | None = None) -> None:
        self._retrieved_at = retrieved_at or date.today()

    def fetch(self, request: DataRequest) -> RawResponse:
        _validate_requested_metrics(request)
        station_id, year = _parse_station_year(request.geography)
        query = {
            "dataset": GSOY_DATASET,
            "dataTypes": "SNOW",
            "stations": station_id,
            "startDate": f"{year}-01-01",
            "endDate": f"{year}-12-31",
            "format": "json",
            "includeAttributes": "true",
            "units": "standard",
        }
        url = f"{NCEI_DATA_SERVICE_URL}?{urlencode(query)}"
        try:
            with urlopen(url, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                payload = response.read()
        except (HTTPError, URLError) as exc:
            raise NoaaGsoyError(f"noaa_gsoy request failed: {exc}") from exc
        return RawResponse(
            source_url=url,
            payload=payload,
            checksum=hashlib.sha256(payload).hexdigest(),
        )

    def normalize(self, response: RawResponse) -> list[ObservationRecord]:
        try:
            rows = json.loads(response.payload)
        except json.JSONDecodeError as exc:
            raise NoaaGsoyError(f"noaa_gsoy returned invalid JSON: {exc}") from exc
        if not isinstance(rows, list):
            raise NoaaGsoyError("noaa_gsoy returned an unexpected response shape")
        if not rows:
            return []
        if len(rows) != 1 or not isinstance(rows[0], dict):
            raise NoaaGsoyError("noaa_gsoy returned multiple or malformed station-year rows")

        row = rows[0]
        try:
            station_id = _required_string(row, "STATION")
            year = _parse_year(_required_string(row, "DATE"))
            snow_text = _required_string(row, "SNOW")
        except NoaaGsoyError:
            raise
        attributes = row.get("SNOW_ATTRIBUTES")
        if not isinstance(attributes, str):
            raise NoaaGsoyError("noaa_gsoy response omits SNOW_ATTRIBUTES")
        if _measurement_flag(attributes):
            # A trace or otherwise flagged amount is not a precise numeric observation.
            # Returning no evidence leaves this critical gate UNKNOWN instead of inventing zero.
            return []
        _validate_response_identity(response.source_url, station_id, year)
        try:
            snowfall_inches = float(snow_text.strip())
        except ValueError as exc:
            raise NoaaGsoyError(
                f"noaa_gsoy returned a non-numeric SNOW value: {snow_text!r}"
            ) from exc

        return [
            ObservationRecord(
                # The orchestrator replaces this placeholder with the caller's place_id.
                # Station identity remains in the source title and request URL.
                place=PlaceRecord(
                    place_id=f"noaa_{station_id}",
                    name=station_id,
                    state="US",
                    geography_type="station",
                ),
                metric_id=SNOWFALL_METRIC_ID,
                raw_value=snowfall_inches,
                observed_period=str(year),
                observed_at=date(year, 12, 31),
                source=SourceRecord(
                    url=response.source_url,
                    title=(
                        f"NOAA NCEI Global Summary of the Year: total annual snowfall "
                        f"(SNOW) at station {station_id}; station-level evidence, "
                        "not a town aggregate"
                    ),
                    publisher="NOAA National Centers for Environmental Information",
                    tier=SourceTier.A,
                    retrieved_at=self._retrieved_at,
                    geography="station",
                    confidence=Confidence.HIGH,
                    synthetic=False,
                ),
            )
        ]

    def validate(self, observations: list[ObservationRecord]) -> ValidationResult:
        errors: list[str] = []
        for observation in observations:
            if observation.metric_id != SNOWFALL_METRIC_ID:
                errors.append(f"{observation.metric_id}: unsupported metric")
            if observation.source.tier is not SourceTier.A:
                errors.append(f"{observation.metric_id}: expected tier A source")
            if (
                observation.source.publisher
                != "NOAA National Centers for Environmental Information"
            ):
                errors.append(f"{observation.metric_id}: unexpected publisher")
            if observation.source.geography != "station":
                errors.append(f"{observation.metric_id}: expected station-level provenance")
            if observation.raw_value < 0:
                errors.append(f"{observation.metric_id}: negative snowfall")
        return ValidationResult(valid=not errors, errors=tuple(errors))


def _validate_requested_metrics(request: DataRequest) -> None:
    unknown = sorted(set(request.metric_ids) - {SNOWFALL_METRIC_ID})
    if unknown:
        raise NoaaGsoyError(f"noaa_gsoy does not support metrics: {unknown}")


def _parse_station_year(geography: str) -> tuple[str, int]:
    try:
        station_id, year_text = geography.rsplit(":", 1)
    except ValueError as exc:
        raise NoaaGsoyError(
            f"geography must be '<NCEI station id>:<completed calendar year>', got {geography!r}"
        ) from exc
    if not station_id or not station_id.replace("-", "").isalnum():
        raise NoaaGsoyError(f"invalid NCEI station id: {station_id!r}")
    return station_id, _parse_year(year_text)


def _parse_year(value: str) -> int:
    try:
        year = int(value)
    except ValueError as exc:
        raise NoaaGsoyError(f"invalid calendar year: {value!r}") from exc
    if not 1763 <= year < date.today().year:
        raise NoaaGsoyError(f"calendar year is outside GSOY's supported range: {year}")
    return year


def _required_string(row: dict[str, object], field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise NoaaGsoyError(f"noaa_gsoy response omits {field}")
    return value


def _measurement_flag(attributes: str) -> str:
    # GSOY documents SNOW_ATTRIBUTES as "M,S", where M is the measurement flag.
    return attributes.split(",", 1)[0].strip()


def _validate_response_identity(source_url: str, station_id: str, year: int) -> None:
    """Ensure the returned station-year is the one embedded in retained provenance."""
    query = parse_qs(urlparse(source_url).query)
    expected_start = f"{year}-01-01"
    expected_end = f"{year}-12-31"
    if (
        query.get("dataset") != [GSOY_DATASET]
        or query.get("dataTypes") != ["SNOW"]
        or query.get("stations") != [station_id]
        or query.get("startDate") != [expected_start]
        or query.get("endDate") != [expected_end]
        or query.get("units") != ["standard"]
    ):
        raise NoaaGsoyError("noaa_gsoy response does not match its retained station-year request")
