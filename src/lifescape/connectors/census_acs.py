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

from lifescape.connectors.base import DataRequest, RawResponse, ValidationResult
from lifescape.models import (
    Confidence,
    ObservationRecord,
    PlaceRecord,
    SourceRecord,
    SourceTier,
)

ACS_DATASET: Final = "acs/acs5/profile"
ACS_CATALOG_URL: Final = "https://api.census.gov/data.json"
ACS_BASE_URL_TEMPLATE: Final = "https://api.census.gov/data/{year}/" + ACS_DATASET

# Maps this engine's metric ids to ACS Data Profile variable codes.
METRIC_VARIABLES: Final[dict[str, str]] = {
    "education_attainment": "DP02_0068PE",  # percent age 25+ with bachelor's degree or higher
}

# Component variables for the distress_index proxy: an unweighted average of
# poverty rate, unemployment rate, and vacant housing rate. This is NOT an
# official Census statistic — it is a derived proxy documented here and in
# the resulting SourceRecord.title so reports never present it as such.
DISTRESS_INDEX_COMPONENT_VARIABLES: Final[tuple[str, ...]] = (
    "DP03_0128PE",  # percent of people below the poverty level
    "DP03_0009PE",  # unemployment rate, civilian labor force
    "DP04_0003PE",  # percent of housing units that are vacant
)

REQUEST_TIMEOUT_SECONDS: Final = 10
API_KEY_ENV_VAR: Final = "CENSUS_API_KEY"
# ACS marks a suppressed or unreliable estimate with this sentinel instead of null.
ACS_MISSING_VALUE_SENTINEL: Final = -666666666

DISTRESS_INDEX_METRIC_ID: Final = "distress_index"


class CensusAcsError(ValueError):
    """Raised when the Census ACS API cannot be fetched or returns an unexpected shape."""


class CensusAcsConnector:
    """Fetches and normalizes place-level American Community Survey estimates.

    Requires a free Census API key (https://api.census.gov/data/key_signup.html),
    passed explicitly or read from the CENSUS_API_KEY environment variable — the
    API returns HTTP 200 with an HTML "Missing Key" body otherwise, not an error status.
    """

    name = "census_acs"
    supported_metric_ids = (*METRIC_VARIABLES, DISTRESS_INDEX_METRIC_ID)

    def __init__(
        self,
        *,
        retrieved_at: date | None = None,
        api_key: str | None = None,
        acs_year: int | None = None,
    ) -> None:
        self._retrieved_at = retrieved_at or date.today()
        self._api_key = api_key or os.environ.get(API_KEY_ENV_VAR)
        if acs_year is not None and (type(acs_year) is not int or acs_year < 2009):
            raise CensusAcsError(
                "acs_year must be an ACS 5-Year Data Profile vintage (2009 or later)"
            )
        self._acs_year = acs_year

    def fetch(self, request: DataRequest) -> RawResponse:
        if not self._api_key:
            raise CensusAcsError(
                f"census_acs requires an API key; set {API_KEY_ENV_VAR} or pass api_key= "
                "(sign up free at https://api.census.gov/data/key_signup.html)"
            )
        unknown = sorted(set(request.metric_ids) - set(self.supported_metric_ids))
        if unknown:
            raise CensusAcsError(f"census_acs does not support metrics: {unknown}")
        try:
            state_fips, place_fips = request.geography.split(":", 1)
        except ValueError as exc:
            raise CensusAcsError(
                f"geography must be '{{state_fips}}:{{place_fips}}', got {request.geography!r}"
            ) from exc
        if not (state_fips.isdigit() and len(state_fips) == 2 and _is_known_state_fips(state_fips)):
            raise CensusAcsError(f"unknown state FIPS code: {state_fips!r}")
        if not (place_fips.isdigit() and len(place_fips) == 5):
            raise CensusAcsError(f"place FIPS code must be five digits, got {place_fips!r}")

        variables = sorted(
            {
                variable
                for metric_id in request.metric_ids
                for variable in _acs_variables_for(metric_id)
            }
        )
        query = {
            "get": ",".join(["NAME", *variables]),
            "for": f"place:{place_fips}",
            "in": f"state:{state_fips}",
            "key": self._api_key,
        }
        acs_year = self._resolve_acs_year()
        url = f"{ACS_BASE_URL_TEMPLATE.format(year=acs_year)}?{urlencode(query)}"
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

        place = PlaceRecord(
            place_id=f"census_{state_fips}_{place_fips}",
            name=place_name,
            state=_state_abbreviation(state_fips),
            geography_type="town",
        )
        acs_year = self._resolved_acs_year_for_normalization()
        observed_period = f"{acs_year - 4}-{acs_year}"
        observed_at = date(acs_year, 12, 31)

        observations: list[ObservationRecord] = []
        for metric_id, variable in METRIC_VARIABLES.items():
            raw_value = _read_acs_value(record, variable)
            if raw_value is None:
                continue
            observations.append(
                ObservationRecord(
                    place=place,
                    metric_id=metric_id,
                    raw_value=raw_value,
                    observed_period=observed_period,
                    observed_at=observed_at,
                    source=SourceRecord(
                        url=response.source_url,
                        title=(
                            f"American Community Survey {acs_year} 5-Year Estimates, Data Profile"
                        ),
                        publisher="U.S. Census Bureau",
                        tier=SourceTier.A,
                        retrieved_at=self._retrieved_at,
                        geography="town",
                        confidence=Confidence.HIGH,
                        synthetic=False,
                    ),
                )
            )

        component_values = [
            value
            for variable in DISTRESS_INDEX_COMPONENT_VARIABLES
            if (value := _read_acs_value(record, variable)) is not None
        ]
        if len(component_values) == len(DISTRESS_INDEX_COMPONENT_VARIABLES):
            observations.append(
                ObservationRecord(
                    place=place,
                    metric_id=DISTRESS_INDEX_METRIC_ID,
                    raw_value=sum(component_values) / len(component_values),
                    observed_period=observed_period,
                    observed_at=observed_at,
                    source=SourceRecord(
                        url=response.source_url,
                        title=(
                            f"Derived from American Community Survey {acs_year} 5-Year "
                            "Estimates, Data Profile: unweighted average of poverty rate, "
                            "unemployment rate, and vacant housing rate (not an official "
                            "Census statistic)"
                        ),
                        publisher="U.S. Census Bureau",
                        tier=SourceTier.A,
                        retrieved_at=self._retrieved_at,
                        geography="town",
                        confidence=Confidence.HIGH,
                        synthetic=False,
                    ),
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

    def _resolve_acs_year(self) -> int:
        """Return an explicitly pinned vintage or discover the newest published one.

        Census's data catalog is the release authority.  We deliberately do not
        derive a vintage from today's date: an ACS release is published late in
        the following calendar year, so that would risk requesting an
        unavailable or not-yet-published dataset.
        """
        if self._acs_year is not None:
            return self._acs_year

        try:
            with urlopen(ACS_CATALOG_URL, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                payload = response.read()
        except (HTTPError, URLError) as exc:
            raise CensusAcsError(
                f"census_acs could not retrieve the Census data catalog: {exc}"
            ) from exc

        try:
            catalog = json.loads(payload)
            datasets = catalog["dataset"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise CensusAcsError(
                "census_acs received an invalid Census data catalog response"
            ) from exc
        if not isinstance(datasets, list):
            raise CensusAcsError("census_acs received an invalid Census data catalog response")

        published_vintages = [
            dataset["c_vintage"]
            for dataset in datasets
            if isinstance(dataset, dict)
            and dataset.get("c_dataset") == ACS_DATASET.split("/")
            and dataset.get("c_isAvailable") is True
            and isinstance(dataset.get("c_vintage"), int)
            and not isinstance(dataset["c_vintage"], bool)
        ]
        if not published_vintages:
            raise CensusAcsError(
                "census_acs could not find a published ACS 5-Year Data Profile vintage "
                "in the Census data catalog"
            )
        self._acs_year = max(published_vintages)
        return self._acs_year

    def _resolved_acs_year_for_normalization(self) -> int:
        if self._acs_year is None:
            raise CensusAcsError(
                "census_acs cannot normalize before fetching and resolving an ACS vintage; "
                "pass acs_year to normalize an independently obtained response"
            )
        return self._acs_year


# Census "in=state:" FIPS codes and USPS abbreviations for every state,
# district, and territory whose Census geography is represented by this
# connector.  The map is deliberately complete rather than tied to the
# benchmark configuration, so live evidence can be collected nationwide.
_STATE_FIPS_TO_ABBREVIATION: Final[dict[str, str]] = {
    "01": "AL",
    "02": "AK",
    "04": "AZ",
    "05": "AR",
    "06": "CA",
    "08": "CO",
    "09": "CT",
    "10": "DE",
    "11": "DC",
    "12": "FL",
    "13": "GA",
    "15": "HI",
    "17": "IL",
    "18": "IN",
    "19": "IA",
    "20": "KS",
    "21": "KY",
    "22": "LA",
    "23": "ME",
    "24": "MD",
    "25": "MA",
    "26": "MI",
    "27": "MN",
    "28": "MS",
    "29": "MO",
    "30": "MT",
    "31": "NE",
    "32": "NV",
    "33": "NH",
    "34": "NJ",
    "35": "NM",
    "36": "NY",
    "37": "NC",
    "38": "ND",
    "39": "OH",
    "40": "OK",
    "41": "OR",
    "42": "PA",
    "44": "RI",
    "45": "SC",
    "46": "SD",
    "47": "TN",
    "48": "TX",
    "49": "UT",
    "50": "VT",
    "51": "VA",
    "53": "WA",
    "54": "WV",
    "55": "WI",
    "56": "WY",
    "60": "AS",
    "66": "GU",
    "69": "MP",
    "72": "PR",
    "78": "VI",
}


def _state_abbreviation(state_fips: str) -> str:
    try:
        return _STATE_FIPS_TO_ABBREVIATION[state_fips]
    except KeyError as exc:
        raise CensusAcsError(f"unknown state FIPS code: {state_fips!r}") from exc


def _is_known_state_fips(state_fips: str) -> bool:
    return state_fips in _STATE_FIPS_TO_ABBREVIATION


def _acs_variables_for(metric_id: str) -> tuple[str, ...]:
    if metric_id == DISTRESS_INDEX_METRIC_ID:
        return DISTRESS_INDEX_COMPONENT_VARIABLES
    return (METRIC_VARIABLES[metric_id],)


def _read_acs_value(record: dict[str, str | None], variable: str) -> float | None:
    value = record.get(variable)
    if value is None:
        return None
    raw_value = float(value)
    if raw_value == ACS_MISSING_VALUE_SENTINEL:
        return None
    return raw_value
