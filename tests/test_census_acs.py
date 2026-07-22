import hashlib
import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lifescape.config import load_metrics
from lifescape.connectors import DataRequest
from lifescape.connectors.base import RawResponse
from lifescape.connectors.census_acs import CensusAcsConnector, CensusAcsError

SAMPLE_PAYLOAD: list[list[str]] = [
    ["NAME", "DP02_0068PE", "state", "place"],
    ["Lake Geneva city, Wisconsin", "42.1", "55", "43075"],
]


def _mock_response(payload: list[list[str]]) -> MagicMock:
    handle = MagicMock()
    handle.read.return_value = json.dumps(payload).encode()
    handle.__enter__.return_value = handle
    handle.__exit__.return_value = False
    return handle


@pytest.fixture
def connector() -> CensusAcsConnector:
    return CensusAcsConnector(retrieved_at=date(2026, 1, 1), api_key="test-key", acs_year=2024)


def _fetch_with_payload(connector: CensusAcsConnector, payload: list[list[str]]) -> RawResponse:
    with patch(
        "lifescape.connectors.census_acs.urlopen",
        return_value=_mock_response(payload),
    ):
        return connector.fetch(
            DataRequest(geography="55:43075", metric_ids=("education_attainment",))
        )


def test_fetch_builds_request_url_and_checksum(connector: CensusAcsConnector) -> None:
    request = DataRequest(geography="55:43075", metric_ids=("education_attainment",))
    with patch(
        "lifescape.connectors.census_acs.urlopen",
        return_value=_mock_response(SAMPLE_PAYLOAD),
    ) as mock_urlopen:
        response = connector.fetch(request)
    assert mock_urlopen.call_count == 1
    called_url = mock_urlopen.call_args[0][0]
    assert "state%3A55" in called_url
    assert "place%3A43075" in called_url
    assert "DP02_0068PE" in called_url
    assert "key=test-key" in called_url
    assert "test-key" not in response.source_url
    assert "key=REDACTED" in response.source_url
    assert response.checksum == hashlib.sha256(response.payload).hexdigest()


def test_fetch_redacts_key_containing_url_unsafe_characters() -> None:
    connector = CensusAcsConnector(
        retrieved_at=date(2026, 1, 1), api_key="test key/+value", acs_year=2024
    )
    request = DataRequest(geography="55:43075", metric_ids=("education_attainment",))
    with patch(
        "lifescape.connectors.census_acs.urlopen",
        return_value=_mock_response(SAMPLE_PAYLOAD),
    ):
        response = connector.fetch(request)
    assert "test key/+value" not in response.source_url
    assert "key=REDACTED" in response.source_url


def test_fetch_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CENSUS_API_KEY", raising=False)
    connector = CensusAcsConnector()
    request = DataRequest(geography="55:43075", metric_ids=("education_attainment",))
    with pytest.raises(CensusAcsError, match="requires an API key"):
        connector.fetch(request)


def test_fetch_reads_api_key_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CENSUS_API_KEY", "env-key")
    connector = CensusAcsConnector(retrieved_at=date(2026, 1, 1), acs_year=2024)
    request = DataRequest(geography="55:43075", metric_ids=("education_attainment",))
    with patch(
        "lifescape.connectors.census_acs.urlopen",
        return_value=_mock_response(SAMPLE_PAYLOAD),
    ) as mock_urlopen:
        connector.fetch(request)
    assert "key=env-key" in mock_urlopen.call_args[0][0]


def test_fetch_rejects_unsupported_metric(connector: CensusAcsConnector) -> None:
    request = DataRequest(geography="55:43075", metric_ids=("median_sale_price",))
    with pytest.raises(CensusAcsError, match="does not support metrics"):
        connector.fetch(request)


def test_fetch_rejects_malformed_geography(connector: CensusAcsConnector) -> None:
    request = DataRequest(geography="not-a-geography", metric_ids=("education_attainment",))
    with pytest.raises(CensusAcsError, match=r"state_fips.*place_fips"):
        connector.fetch(request)


def test_normalize_builds_observation_with_provenance(connector: CensusAcsConnector) -> None:
    response = _fetch_with_payload(connector, SAMPLE_PAYLOAD)
    observations = connector.normalize(response)
    assert len(observations) == 1
    observation = observations[0]
    assert observation.metric_id == "education_attainment"
    assert observation.raw_value == pytest.approx(42.1)
    assert observation.place.place_id == "census_55_43075"
    assert observation.place.name == "Lake Geneva city"
    assert observation.place.state == "WI"
    assert observation.source.publisher == "U.S. Census Bureau"
    assert observation.source.tier.value == "A"
    assert observation.source.synthetic is False
    assert observation.source.retrieved_at == date(2026, 1, 1)
    assert observation.observed_period == "2020-2024"
    assert observation.observed_at == date(2024, 12, 31)


def test_fetch_discovers_latest_published_acs_data_profile_vintage() -> None:
    connector = CensusAcsConnector(retrieved_at=date(2026, 1, 1), api_key="test-key")
    catalog = {
        "dataset": [
            {"c_dataset": ["acs", "acs5", "profile"], "c_isAvailable": True, "c_vintage": 2023},
            {"c_dataset": ["acs", "acs5", "profile"], "c_isAvailable": False, "c_vintage": 2025},
            {"c_dataset": ["acs", "acs5", "profile"], "c_isAvailable": True, "c_vintage": 2024},
        ]
    }
    with patch(
        "lifescape.connectors.census_acs.urlopen",
        side_effect=[_mock_response(catalog), _mock_response(SAMPLE_PAYLOAD)],
    ) as mock_urlopen:
        response = connector.fetch(
            DataRequest(geography="55:43075", metric_ids=("education_attainment",))
        )

    assert mock_urlopen.call_args_list[0].args[0] == "https://api.census.gov/data.json"
    assert (
        "https://api.census.gov/data/2024/acs/acs5/profile?"
        in mock_urlopen.call_args_list[1].args[0]
    )
    observation = connector.normalize(response)[0]
    assert observation.observed_period == "2020-2024"


def test_fetch_rejects_catalog_without_a_published_acs_data_profile() -> None:
    connector = CensusAcsConnector(retrieved_at=date(2026, 1, 1), api_key="test-key")
    catalog = {
        "dataset": [
            {
                "c_dataset": ["acs", "acs1", "profile"],
                "c_isAvailable": True,
                "c_vintage": 2024,
            }
        ]
    }
    with (
        patch("lifescape.connectors.census_acs.urlopen", return_value=_mock_response(catalog)),
        pytest.raises(CensusAcsError, match="could not find a published ACS 5-Year Data Profile"),
    ):
        connector.fetch(DataRequest(geography="55:43075", metric_ids=("education_attainment",)))


def test_normalize_requires_a_resolved_or_explicit_acs_year() -> None:
    connector = CensusAcsConnector(retrieved_at=date(2026, 1, 1), api_key="test-key")
    response = RawResponse(
        source_url="https://example.gov", payload=json.dumps(SAMPLE_PAYLOAD).encode(), checksum="x"
    )
    with pytest.raises(CensusAcsError, match="cannot normalize before fetching and resolving"):
        connector.normalize(response)


def test_normalize_supports_district_of_columbia_place_fips(connector: CensusAcsConnector) -> None:
    payload: list[list[str]] = [
        ["NAME", "DP02_0068PE", "state", "place"],
        ["Washington city, District of Columbia", "63.0", "11", "50000"],
    ]
    response = _fetch_with_payload(connector, payload)
    observation = connector.normalize(response)[0]
    assert observation.place.state == "DC"


def test_normalize_rejects_empty_rows(connector: CensusAcsConnector) -> None:
    response = RawResponse(source_url="https://example.gov", payload=b"[]", checksum="x")
    with pytest.raises(CensusAcsError, match="no data rows"):
        connector.normalize(response)


def test_normalize_rejects_invalid_json(connector: CensusAcsConnector) -> None:
    response = RawResponse(source_url="https://example.gov", payload=b"not json", checksum="x")
    with pytest.raises(CensusAcsError, match="invalid JSON"):
        connector.normalize(response)


def test_normalize_reports_html_error_page_as_key_problem(
    connector: CensusAcsConnector,
) -> None:
    response = RawResponse(
        source_url="https://example.gov",
        payload=b"<html><body>A valid key must be included</body></html>",
        checksum="x",
    )
    with pytest.raises(CensusAcsError, match="invalid, expired, or missing API key"):
        connector.normalize(response)


def test_normalize_rejects_unknown_state_fips(connector: CensusAcsConnector) -> None:
    payload: list[list[str]] = [
        ["NAME", "DP02_0068PE", "state", "place"],
        ["Somewhere city, Nowhere", "10.0", "99", "00000"],
    ]
    response = RawResponse(
        source_url="https://example.gov", payload=json.dumps(payload).encode(), checksum="x"
    )
    with pytest.raises(CensusAcsError, match="unknown state FIPS"):
        connector.normalize(response)


def test_normalize_rejects_response_missing_expected_fields(
    connector: CensusAcsConnector,
) -> None:
    payload: list[list[str]] = [["NAME", "DP02_0068PE"], ["Lake Geneva city, Wisconsin", "42.1"]]
    response = RawResponse(
        source_url="https://example.gov", payload=json.dumps(payload).encode(), checksum="x"
    )
    with pytest.raises(CensusAcsError, match="unexpected response shape"):
        connector.normalize(response)


def test_normalize_skips_acs_suppressed_value_sentinel(connector: CensusAcsConnector) -> None:
    payload: list[list[str]] = [
        ["NAME", "DP02_0068PE", "state", "place"],
        ["Lake Geneva city, Wisconsin", "-666666666", "55", "43075"],
    ]
    response = _fetch_with_payload(connector, payload)
    assert connector.normalize(response) == []


def test_normalize_skips_null_estimate(connector: CensusAcsConnector) -> None:
    payload: list[list] = [
        ["NAME", "DP02_0068PE", "state", "place"],
        ["Lake Geneva city, Wisconsin", None, "55", "43075"],
    ]
    response = RawResponse(
        source_url="https://example.gov", payload=json.dumps(payload).encode(), checksum="x"
    )
    assert connector.normalize(response) == []


def test_validate_accepts_well_formed_observations(connector: CensusAcsConnector) -> None:
    response = _fetch_with_payload(connector, SAMPLE_PAYLOAD)
    observations = connector.normalize(response)
    result = connector.validate(observations)
    assert result.valid
    assert result.errors == ()


def test_validate_flags_negative_estimate(connector: CensusAcsConnector) -> None:
    payload: list[list[str]] = [
        ["NAME", "DP02_0068PE", "state", "place"],
        ["Lake Geneva city, Wisconsin", "-1", "55", "43075"],
    ]
    response = _fetch_with_payload(connector, payload)
    observations = connector.normalize(response)
    result = connector.validate(observations)
    assert not result.valid
    assert any("negative ACS estimate" in error for error in result.errors)


def test_normalized_observation_satisfies_metric_valid_range(
    connector: CensusAcsConnector,
) -> None:
    response = _fetch_with_payload(connector, SAMPLE_PAYLOAD)
    observations = connector.normalize(response)
    metrics = {m.id: m for m in load_metrics(Path("config"))}
    for observation in observations:
        metric = metrics[observation.metric_id]
        assert metric.valid_min <= observation.raw_value <= metric.valid_max


DISTRESS_INDEX_PAYLOAD: list[list[str]] = [
    ["NAME", "DP03_0128PE", "DP03_0009PE", "DP04_0003PE", "state", "place"],
    ["Lake Geneva city, Wisconsin", "6.0", "3.0", "9.0", "55", "43075"],
]


def _fetch_distress_index(connector: CensusAcsConnector, payload: list[list[str]]) -> RawResponse:
    with patch(
        "lifescape.connectors.census_acs.urlopen",
        return_value=_mock_response(payload),
    ):
        return connector.fetch(DataRequest(geography="55:43075", metric_ids=("distress_index",)))


def test_fetch_requests_all_distress_index_component_variables(
    connector: CensusAcsConnector,
) -> None:
    request = DataRequest(geography="55:43075", metric_ids=("distress_index",))
    with patch(
        "lifescape.connectors.census_acs.urlopen",
        return_value=_mock_response(DISTRESS_INDEX_PAYLOAD),
    ) as mock_urlopen:
        connector.fetch(request)
    called_url = mock_urlopen.call_args[0][0]
    assert "DP03_0128PE" in called_url
    assert "DP03_0009PE" in called_url
    assert "DP04_0003PE" in called_url


def test_normalize_averages_distress_index_components(connector: CensusAcsConnector) -> None:
    response = _fetch_distress_index(connector, DISTRESS_INDEX_PAYLOAD)
    observations = connector.normalize(response)
    assert len(observations) == 1
    observation = observations[0]
    assert observation.metric_id == "distress_index"
    assert observation.raw_value == pytest.approx((6.0 + 3.0 + 9.0) / 3)
    assert "not an official Census statistic" in observation.source.title
    assert observation.source.publisher == "U.S. Census Bureau"


def test_normalize_omits_distress_index_when_any_component_missing(
    connector: CensusAcsConnector,
) -> None:
    payload: list[list] = [
        ["NAME", "DP03_0128PE", "DP03_0009PE", "DP04_0003PE", "state", "place"],
        ["Lake Geneva city, Wisconsin", "6.0", None, "9.0", "55", "43075"],
    ]
    response = RawResponse(
        source_url="https://example.gov", payload=json.dumps(payload).encode(), checksum="x"
    )
    assert connector.normalize(response) == []


def test_normalize_omits_distress_index_when_a_component_is_suppressed(
    connector: CensusAcsConnector,
) -> None:
    payload: list[list[str]] = [
        ["NAME", "DP03_0128PE", "DP03_0009PE", "DP04_0003PE", "state", "place"],
        ["Lake Geneva city, Wisconsin", "6.0", "-666666666", "9.0", "55", "43075"],
    ]
    response = RawResponse(
        source_url="https://example.gov", payload=json.dumps(payload).encode(), checksum="x"
    )
    assert connector.normalize(response) == []


def test_fetch_requesting_both_metrics_dedupes_and_requests_union_of_variables(
    connector: CensusAcsConnector,
) -> None:
    request = DataRequest(
        geography="55:43075", metric_ids=("education_attainment", "distress_index")
    )
    payload: list[list[str]] = [
        [
            "NAME",
            "DP02_0068PE",
            "DP03_0128PE",
            "DP03_0009PE",
            "DP04_0003PE",
            "state",
            "place",
        ],
        ["Lake Geneva city, Wisconsin", "42.1", "6.0", "3.0", "9.0", "55", "43075"],
    ]
    with patch(
        "lifescape.connectors.census_acs.urlopen",
        return_value=_mock_response(payload),
    ) as mock_urlopen:
        response = connector.fetch(request)
    called_url = mock_urlopen.call_args[0][0]
    assert "DP02_0068PE" in called_url
    assert "DP03_0128PE" in called_url
    observations = connector.normalize(response)
    assert {observation.metric_id for observation in observations} == {
        "education_attainment",
        "distress_index",
    }


def test_normalized_distress_index_satisfies_metric_valid_range(
    connector: CensusAcsConnector,
) -> None:
    response = _fetch_distress_index(connector, DISTRESS_INDEX_PAYLOAD)
    observations = connector.normalize(response)
    metrics = {m.id: m for m in load_metrics(Path("config"))}
    for observation in observations:
        metric = metrics[observation.metric_id]
        assert metric.valid_min <= observation.raw_value <= metric.valid_max


@pytest.mark.parametrize("component_value", ["0.0", "100.0"])
def test_normalize_distress_index_average_stays_within_bounds_at_extremes(
    connector: CensusAcsConnector, component_value: str
) -> None:
    payload: list[list[str]] = [
        ["NAME", "DP03_0128PE", "DP03_0009PE", "DP04_0003PE", "state", "place"],
        [
            "Lake Geneva city, Wisconsin",
            component_value,
            component_value,
            component_value,
            "55",
            "43075",
        ],
    ]
    response = _fetch_distress_index(connector, payload)
    observations = connector.normalize(response)
    assert observations[0].raw_value == pytest.approx(float(component_value))
    assert 0 <= observations[0].raw_value <= 100


def test_validate_accepts_well_formed_distress_index_observation(
    connector: CensusAcsConnector,
) -> None:
    response = _fetch_distress_index(connector, DISTRESS_INDEX_PAYLOAD)
    observations = connector.normalize(response)
    result = connector.validate(observations)
    assert result.valid
    assert result.errors == ()
