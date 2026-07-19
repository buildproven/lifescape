from datetime import date

import pytest

from lifescape.connectors.base import DataRequest, RawResponse, ValidationResult
from lifescape.connectors.orchestrate import PlaceRequest, fetch_live_observations
from lifescape.models import (
    Confidence,
    ObservationRecord,
    PlaceRecord,
    SourceRecord,
    SourceTier,
)


def _observation(place_id: str, metric_id: str, value: float) -> ObservationRecord:
    return ObservationRecord(
        place=PlaceRecord(place_id=place_id, name="Some Place", state="WI", geography_type="town"),
        metric_id=metric_id,
        raw_value=value,
        observed_period="2019-2023",
        observed_at=date(2023, 12, 31),
        source=SourceRecord(
            url="https://example.gov",
            title="Example source",
            publisher="Example Publisher",
            tier=SourceTier.A,
            retrieved_at=date(2026, 1, 1),
            geography="town",
            confidence=Confidence.HIGH,
            synthetic=False,
        ),
    )


class FakeConnector:
    name = "fake"
    supported_metric_ids = ("education_attainment",)

    def __init__(self, *, fail_fetch: bool = False, fail_validate: bool = False) -> None:
        self.fail_fetch = fail_fetch
        self.fail_validate = fail_validate
        self.fetch_calls: list[DataRequest] = []

    def fetch(self, request: DataRequest) -> RawResponse:
        self.fetch_calls.append(request)
        if self.fail_fetch:
            raise ValueError("simulated fetch failure")
        return RawResponse(source_url="https://example.gov", payload=b"{}", checksum="x")

    def normalize(self, response: RawResponse) -> list[ObservationRecord]:
        del response
        return [_observation("connector_native_id", "education_attainment", 42.0)]

    def validate(self, observations: list[ObservationRecord]) -> ValidationResult:
        del observations
        if self.fail_validate:
            return ValidationResult(valid=False, errors=("simulated validation failure",))
        return ValidationResult(valid=True)


class NoMetricsConnector:
    name = "empty"
    supported_metric_ids: tuple[str, ...] = ()

    def fetch(self, request: DataRequest) -> RawResponse:
        raise AssertionError("should never be called when supported_metric_ids is empty")

    def normalize(self, response: RawResponse) -> list[ObservationRecord]:
        raise AssertionError("should never be called when supported_metric_ids is empty")

    def validate(self, observations: list[ObservationRecord]) -> ValidationResult:
        raise AssertionError("should never be called when supported_metric_ids is empty")


def test_fetch_live_observations_rebinds_place_id_to_caller_identity() -> None:
    connector = FakeConnector()
    places = [PlaceRequest(place_id="lake_geneva_wi", geography="55:43075")]

    observations = fetch_live_observations([connector], places)

    assert len(observations) == 1
    assert observations[0].place.place_id == "lake_geneva_wi"
    assert connector.fetch_calls[0].geography == "55:43075"
    assert connector.fetch_calls[0].metric_ids == ("education_attainment",)


def test_fetch_live_observations_skips_place_on_fetch_failure() -> None:
    connector = FakeConnector(fail_fetch=True)
    places = [PlaceRequest(place_id="lake_geneva_wi", geography="55:43075")]
    events: list[tuple[str, dict[str, object]]] = []

    observations = fetch_live_observations(
        [connector], places, on_event=lambda event, fields: events.append((event, fields))
    )

    assert observations == ()
    assert events == [
        (
            "connector_fetch_failed",
            {
                "connector": "fake",
                "place_id": "lake_geneva_wi",
                "error": "simulated fetch failure",
            },
        )
    ]


def test_fetch_live_observations_skips_place_on_validation_failure() -> None:
    connector = FakeConnector(fail_validate=True)
    places = [PlaceRequest(place_id="lake_geneva_wi", geography="55:43075")]
    events: list[tuple[str, dict[str, object]]] = []

    observations = fetch_live_observations(
        [connector], places, on_event=lambda event, fields: events.append((event, fields))
    )

    assert observations == ()
    assert events == [
        (
            "connector_validation_failed",
            {
                "connector": "fake",
                "place_id": "lake_geneva_wi",
                "errors": ["simulated validation failure"],
            },
        )
    ]


def test_fetch_live_observations_continues_after_one_place_fails() -> None:
    connector = FakeConnector()
    original_fetch = connector.fetch
    call_count = 0

    def flaky_fetch(request: DataRequest) -> RawResponse:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise ValueError("first place fails")
        return original_fetch(request)

    connector.fetch = flaky_fetch  # type: ignore[method-assign]
    places = [
        PlaceRequest(place_id="failing_town", geography="55:00001"),
        PlaceRequest(place_id="lake_geneva_wi", geography="55:43075"),
    ]

    observations = fetch_live_observations([connector], places)

    assert len(observations) == 1
    assert observations[0].place.place_id == "lake_geneva_wi"


def test_fetch_live_observations_skips_connector_with_no_supported_metrics() -> None:
    places = [PlaceRequest(place_id="lake_geneva_wi", geography="55:43075")]

    observations = fetch_live_observations([NoMetricsConnector()], places)

    assert observations == ()


def test_fetch_live_observations_without_event_sink_does_not_raise() -> None:
    connector = FakeConnector(fail_fetch=True)
    places = [PlaceRequest(place_id="lake_geneva_wi", geography="55:43075")]

    observations = fetch_live_observations([connector], places)

    assert observations == ()


def test_fetch_live_observations_with_no_places_returns_empty() -> None:
    observations = fetch_live_observations([FakeConnector()], [])
    assert observations == ()


def test_fetch_live_observations_with_no_connectors_returns_empty() -> None:
    places = [PlaceRequest(place_id="lake_geneva_wi", geography="55:43075")]
    observations = fetch_live_observations([], places)
    assert observations == ()


def test_fetch_live_observations_with_multiple_places_and_connectors() -> None:
    connector_a = FakeConnector()
    connector_b = FakeConnector()
    places = [
        PlaceRequest(place_id="lake_geneva_wi", geography="55:43075"),
        PlaceRequest(place_id="another_town", geography="17:00001"),
    ]

    observations = fetch_live_observations([connector_a, connector_b], places)

    assert len(observations) == 4
    assert {obs.place.place_id for obs in observations} == {"lake_geneva_wi", "another_town"}


@pytest.mark.parametrize("place_id", ["lake_geneva_wi", "another_town_id"])
def test_place_request_stores_identity(place_id: str) -> None:
    request = PlaceRequest(place_id=place_id, geography="55:43075")
    assert request.place_id == place_id
    assert request.geography == "55:43075"
