import hashlib
import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from retirement_engine.config import load_metrics
from retirement_engine.connectors import DataRequest
from retirement_engine.connectors.base import RawResponse
from retirement_engine.connectors.census_acs import CensusAcsConnector, CensusAcsError

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
    return CensusAcsConnector(retrieved_at=date(2026, 1, 1), api_key="test-key")


def _fetch_with_payload(connector: CensusAcsConnector, payload: list[list[str]]) -> RawResponse:
    with patch(
        "retirement_engine.connectors.census_acs.urlopen",
        return_value=_mock_response(payload),
    ):
        return connector.fetch(
            DataRequest(geography="55:43075", metric_ids=("education_attainment",))
        )


def test_fetch_builds_request_url_and_checksum(connector: CensusAcsConnector) -> None:
    request = DataRequest(geography="55:43075", metric_ids=("education_attainment",))
    with patch(
        "retirement_engine.connectors.census_acs.urlopen",
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
    connector = CensusAcsConnector(retrieved_at=date(2026, 1, 1), api_key="test key/+value")
    request = DataRequest(geography="55:43075", metric_ids=("education_attainment",))
    with patch(
        "retirement_engine.connectors.census_acs.urlopen",
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
    connector = CensusAcsConnector(retrieved_at=date(2026, 1, 1))
    request = DataRequest(geography="55:43075", metric_ids=("education_attainment",))
    with patch(
        "retirement_engine.connectors.census_acs.urlopen",
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
