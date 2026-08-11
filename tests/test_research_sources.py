from __future__ import annotations

from datetime import date

import pytest

from lifescape.connectors.base import DataRequest, RawResponse, ValidationResult
from lifescape.models import Confidence, ObservationRecord, PlaceRecord, SourceRecord, SourceTier
from lifescape.research import DiscoveryLead, SearchBrief, create_packet
from lifescape.research_sources import ConnectorEvidenceProvider


class FakeConnector:
    name = "fake_source"
    supported_metric_ids = ("distress_index",)

    def fetch(self, request: DataRequest) -> RawResponse:
        assert request.geography == "fixture:town"
        return RawResponse(source_url="https://example.gov/data", payload=b"{}", checksum="abc")

    def normalize(self, response: RawResponse) -> list[ObservationRecord]:
        del response
        return [
            ObservationRecord(
                place=PlaceRecord(place_id="asheville_nc", name="Asheville", state="NC"),
                metric_id="distress_index",
                raw_value=2,
                observed_period="2025",
                observed_at=date(2025, 12, 31),
                source=SourceRecord(
                    url="https://example.gov/data",
                    title="Official fixture",
                    publisher="Official source",
                    tier=SourceTier.A,
                    retrieved_at=date(2026, 1, 1),
                    geography="town",
                    confidence=Confidence.HIGH,
                ),
            )
        ]

    def validate(self, observations: list[ObservationRecord]) -> ValidationResult:
        return ValidationResult(valid=bool(observations))


class FailingConnector:
    name = "failing_source"
    supported_metric_ids = ("distress_index",)

    def fetch(self, request: DataRequest) -> RawResponse:
        del request
        raise ValueError("fixture fetch failed")

    def normalize(self, response: RawResponse) -> list[ObservationRecord]:
        del response
        return []

    def validate(self, observations: list[ObservationRecord]) -> ValidationResult:
        del observations
        return ValidationResult(valid=True)


class InvalidConnector(FakeConnector):
    name = "invalid_source"

    def validate(self, observations: list[ObservationRecord]) -> ValidationResult:
        del observations
        return ValidationResult(valid=False, errors=("fixture validation failed",))


def test_connector_provider_returns_observations_and_reports_missing_geography() -> None:
    packet = create_packet(
        SearchBrief(preferences="A walkable retirement town with outdoor access."),
        (
            DiscoveryLead(
                place=PlaceRecord(place_id="asheville_nc", name="Asheville", state="NC"),
                rationale="fixture lead",
                connector_geographies={"fake_source": "fixture:town"},
            ),
            DiscoveryLead(
                place=PlaceRecord(place_id="bend_or", name="Bend", state="OR"),
                rationale="missing geography",
            ),
        ),
    )

    result = ConnectorEvidenceProvider(connectors=(FakeConnector(),)).fetch(packet)

    assert {(item.place.place_id, item.metric_id) for item in result.observations} == {
        ("asheville_nc", "distress_index")
    }
    assert result.errors == {
        "bend_or": ("fake_source: explicit source geography is not configured",)
    }


def test_connector_provider_loads_system_geography_configuration(monkeypatch) -> None:
    monkeypatch.setenv(
        "LIFESCAPE_RESEARCH_GEOGRAPHIES",
        '{"asheville_nc":{"fake_source":"fixture:town"}}',
    )

    provider = ConnectorEvidenceProvider.from_environment()

    assert provider._geographies == {"asheville_nc": {"fake_source": "fixture:town"}}


@pytest.mark.parametrize(
    "raw, message",
    [
        ("not-json", "valid JSON"),
        ("[]", "must be an object"),
        ('{"asheville_nc":"fixture:town"}', "map place IDs to objects"),
        ('{"asheville_nc":{"fake_source":1}}', "values must be strings"),
    ],
)
def test_connector_provider_rejects_invalid_system_geography_configuration(
    monkeypatch, raw: str, message: str
) -> None:
    monkeypatch.setenv("LIFESCAPE_RESEARCH_GEOGRAPHIES", raw)

    with pytest.raises(ValueError, match=message):
        ConnectorEvidenceProvider.from_environment()


def test_connector_provider_reports_fetch_and_validation_failures() -> None:
    packet = create_packet(
        SearchBrief(preferences="A walkable retirement town with outdoor access."),
        (
            DiscoveryLead(
                place=PlaceRecord(place_id="asheville_nc", name="Asheville", state="NC"),
                rationale="fixture lead",
                connector_geographies={
                    "failing_source": "fixture:town",
                    "invalid_source": "fixture:town",
                },
            ),
        ),
    )

    result = ConnectorEvidenceProvider(connectors=(FailingConnector(), InvalidConnector())).fetch(
        packet
    )

    assert result.observations == ()
    assert result.errors["asheville_nc"] == (
        "failing_source: fixture fetch failed",
        "invalid_source: fixture validation failed",
    )
