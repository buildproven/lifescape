import hashlib
import json
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from lifescape.connectors.base import DataRequest, RawResponse
from lifescape.connectors.noaa_gsoy import NoaaGsoyConnector, NoaaGsoyError

SAMPLE_ROW = {
    "DATE": "2024",
    "STATION": "USC00218450",
    "SNOW": "31.6",
    "SNOW_ATTRIBUTES": " ,7",
}


def _mock_response(payload: object) -> MagicMock:
    handle = MagicMock()
    handle.read.return_value = json.dumps(payload).encode()
    handle.__enter__.return_value = handle
    handle.__exit__.return_value = False
    return handle


@pytest.fixture
def connector() -> NoaaGsoyConnector:
    return NoaaGsoyConnector(retrieved_at=date(2026, 1, 1))


def _fetch_with_payload(connector: NoaaGsoyConnector, payload: object) -> RawResponse:
    with patch("lifescape.connectors.noaa_gsoy.urlopen", return_value=_mock_response(payload)):
        return connector.fetch(
            DataRequest(geography="USC00218450:2024", metric_ids=("annual_snowfall",))
        )


def test_fetch_requests_direct_station_year_snowfall_in_standard_units(
    connector: NoaaGsoyConnector,
) -> None:
    with patch(
        "lifescape.connectors.noaa_gsoy.urlopen", return_value=_mock_response([SAMPLE_ROW])
    ) as mock_urlopen:
        response = connector.fetch(
            DataRequest(geography="USC00218450:2024", metric_ids=("annual_snowfall",))
        )

    url = mock_urlopen.call_args.args[0]
    assert "dataset=global-summary-of-the-year" in url
    assert "dataTypes=SNOW" in url
    assert "stations=USC00218450" in url
    assert "startDate=2024-01-01" in url
    assert "endDate=2024-12-31" in url
    assert "units=standard" in url
    assert response.checksum == hashlib.sha256(response.payload).hexdigest()


def test_fetch_rejects_unimplemented_metric(connector: NoaaGsoyConnector) -> None:
    with pytest.raises(NoaaGsoyError, match="does not support metrics"):
        connector.fetch(DataRequest(geography="USC00218450:2024", metric_ids=("distress_index",)))


@pytest.mark.parametrize("geography", ["USC00218450", f"USC00218450:{date.today().year}", "!:2024"])
def test_fetch_rejects_invalid_or_incomplete_station_year(
    connector: NoaaGsoyConnector, geography: str
) -> None:
    with pytest.raises(NoaaGsoyError):
        connector.fetch(DataRequest(geography=geography, metric_ids=("annual_snowfall",)))


def test_normalize_preserves_station_level_provenance(connector: NoaaGsoyConnector) -> None:
    observation = connector.normalize(_fetch_with_payload(connector, [SAMPLE_ROW]))[0]

    assert observation.metric_id == "annual_snowfall"
    assert observation.raw_value == pytest.approx(31.6)
    assert observation.observed_period == "2024"
    assert observation.observed_at == date(2024, 12, 31)
    assert observation.source.publisher == "NOAA National Centers for Environmental Information"
    assert observation.source.geography == "station"
    assert "USC00218450" in observation.source.title
    assert "not a town aggregate" in observation.source.title
    assert observation.source.retrieved_at == date(2026, 1, 1)


def test_normalize_treats_measurement_flag_as_missing_evidence(
    connector: NoaaGsoyConnector,
) -> None:
    flagged = {**SAMPLE_ROW, "SNOW_ATTRIBUTES": "T,7"}
    assert connector.normalize(_fetch_with_payload(connector, [flagged])) == []


@pytest.mark.parametrize("payload", [[], {}, [SAMPLE_ROW, SAMPLE_ROW]])
def test_normalize_rejects_empty_or_ambiguous_response(
    connector: NoaaGsoyConnector, payload: object
) -> None:
    response = _fetch_with_payload(connector, payload)
    if payload == []:
        assert connector.normalize(response) == []
    else:
        with pytest.raises(NoaaGsoyError, match=r"unexpected|multiple"):
            connector.normalize(response)


def test_normalize_rejects_response_not_matching_retained_request(
    connector: NoaaGsoyConnector,
) -> None:
    response = RawResponse(
        source_url="https://example.gov?stations=OTHER",
        payload=json.dumps([SAMPLE_ROW]).encode(),
        checksum="x",
    )
    with pytest.raises(NoaaGsoyError, match="does not match"):
        connector.normalize(response)


def test_validate_rejects_negative_snowfall(connector: NoaaGsoyConnector) -> None:
    observation = connector.normalize(_fetch_with_payload(connector, [SAMPLE_ROW]))[0]
    invalid = observation.model_copy(update={"raw_value": -0.1})
    result = connector.validate([invalid])
    assert not result.valid
    assert "negative snowfall" in result.errors[0]
