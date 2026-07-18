"""Census ACS 5-Year connector for place-level demographic metrics.

Geography identifiers use the Census place FIPS convention: "{state_fips}:{place_fips}".
Discover FIPS codes at https://www.census.gov/library/reference/code-lists/ansi.html.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import date
from typing import Final
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from retirement_engine.connectors.base import DataRequest, RawResponse, ValidationResult
from retirement_engine.models import (
    Confidence,
    ObservationRecord,
    PlaceRecord,
    SourceRecord,
    SourceTier,
)

ACS_YEAR: Final = 2023
ACS_DATASET: Final = "acs/acs5/profile"
ACS_BASE_URL: Final = f"https://api.census.gov/data/{ACS_YEAR}/{ACS_DATASET}"

# Maps this engine's metric ids to ACS Data Profile variable codes.
METRIC_VARIABLES: Final[dict[str, str]] = {
    "education_attainment": "DP02_0068PE",  # percent age 25+ with bachelor's degree or higher
}

REQUEST_TIMEOUT_SECONDS: Final = 10
API_KEY_ENV_VAR: Final = "CENSUS_API_KEY"
# ACS marks a suppressed or unreliable estimate with this sentinel instead of null.
ACS_MISSING_VALUE_SENTINEL: Final = -666666666


class CensusAcsError(ValueError):
    """Raised when the Census ACS API cannot be fetched or returns an unexpected shape."""


class CensusAcsConnector:
    """Fetches and normalizes place-level American Community Survey estimates.

    Requires a free Census API key (https://api.census.gov/data/key_signup.html),
    passed explicitly or read from the CENSUS_API_KEY environment variable — the
    API returns HTTP 200 with an HTML "Missing Key" body otherwise, not an error status.
    """

    name = "census_acs"
    supported_metric_ids = tuple(METRIC_VARIABLES)

    def __init__(self, *, retrieved_at: date | None = None, api_key: str | None = None) -> None:
        self._retrieved_at = retrieved_at or date.today()
        self._api_key = api_key or os.environ.get(API_KEY_ENV_VAR)

    def fetch(self, request: DataRequest) -> RawResponse:
        if not self._api_key:
            raise CensusAcsError(
                f"census_acs requires an API key; set {API_KEY_ENV_VAR} or pass api_key= "
                "(sign up free at https://api.census.gov/data/key_signup.html)"
            )
        unknown = sorted(set(request.metric_ids) - set(METRIC_VARIABLES))
        if unknown:
            raise CensusAcsError(f"census_acs does not support metrics: {unknown}")
        try:
            state_fips, place_fips = request.geography.split(":", 1)
        except ValueError as exc:
            raise CensusAcsError(
                f"geography must be '{{state_fips}}:{{place_fips}}', got {request.geography!r}"
            ) from exc

        variables = [METRIC_VARIABLES[metric_id] for metric_id in request.metric_ids]
        query = {
            "get": ",".join(["NAME", *variables]),
            "for": f"place:{place_fips}",
            "in": f"state:{state_fips}",
            "key": self._api_key,
        }
        url = f"{ACS_BASE_URL}?{urlencode(query)}"
        try:
            with urlopen(url, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                payload = response.read()
        except (HTTPError, URLError) as exc:
            raise CensusAcsError(f"census_acs request failed: {exc}") from exc

        redacted_url = url.replace(urlencode({"key": self._api_key}), "key=REDACTED")
        return RawResponse(
            source_url=redacted_url,
            payload=payload,
            checksum=hashlib.sha256(payload).hexdigest(),
        )

    def normalize(self, response: RawResponse) -> list[ObservationRecord]:
        try:
            rows = json.loads(response.payload)
        except json.JSONDecodeError as exc:
            if response.payload.lstrip().startswith(b"<"):
                raise CensusAcsError(
                    "census_acs returned an HTML error page instead of JSON "
                    "(often an invalid, expired, or missing API key)"
                ) from exc
            raise CensusAcsError(f"census_acs returned invalid JSON: {exc}") from exc
        if not isinstance(rows, list) or len(rows) < 2:
            raise CensusAcsError(f"census_acs returned no data rows: {rows!r}")

        header, data_row = rows[0], rows[1]
        try:
            record = dict(zip(header, data_row, strict=True))
            place_name = record["NAME"].split(",")[0].strip()
            state_fips = record["state"]
            place_fips = record["place"]
        except (ValueError, KeyError) as exc:
            raise CensusAcsError(
                f"census_acs returned an unexpected response shape: {exc}"
            ) from exc

        source = SourceRecord(
            url=response.source_url,
            title=f"American Community Survey {ACS_YEAR} 5-Year Estimates, Data Profile",
            publisher="U.S. Census Bureau",
            tier=SourceTier.A,
            retrieved_at=self._retrieved_at,
            geography="town",
            confidence=Confidence.HIGH,
            synthetic=False,
        )
        place = PlaceRecord(
            place_id=f"census_{state_fips}_{place_fips}",
            name=place_name,
            state=_state_abbreviation(state_fips),
            geography_type="town",
        )

        observations: list[ObservationRecord] = []
        for metric_id, variable in METRIC_VARIABLES.items():
            if variable not in record or record[variable] is None:
                continue
            raw_value = float(record[variable])
            if raw_value == ACS_MISSING_VALUE_SENTINEL:
                continue
            observations.append(
                ObservationRecord(
                    place=place,
                    metric_id=metric_id,
                    raw_value=raw_value,
                    observed_period=f"{ACS_YEAR - 4}-{ACS_YEAR}",
                    observed_at=date(ACS_YEAR, 12, 31),
                    source=source,
                )
            )
        return observations

    def validate(self, observations: list[ObservationRecord]) -> ValidationResult:
        errors: list[str] = []
        for observation in observations:
            if observation.source.tier is not SourceTier.A:
                errors.append(f"{observation.metric_id}: expected tier A source")
            if observation.source.publisher != "U.S. Census Bureau":
                errors.append(f"{observation.metric_id}: unexpected publisher")
            if observation.raw_value < 0:
                errors.append(f"{observation.metric_id}: negative ACS estimate")
        return ValidationResult(valid=not errors, errors=tuple(errors))


# Census "in=state:" FIPS codes for the state abbreviations this engine currently benchmarks.
# Extend as new states are added to config/regions.yaml.
_STATE_FIPS_TO_ABBREVIATION: Final[dict[str, str]] = {
    "17": "IL",
    "18": "IN",
    "27": "MN",
    "37": "NC",
    "55": "WI",
}


def _state_abbreviation(state_fips: str) -> str:
    try:
        return _STATE_FIPS_TO_ABBREVIATION[state_fips]
    except KeyError as exc:
        raise CensusAcsError(f"unknown state FIPS code: {state_fips!r}") from exc
