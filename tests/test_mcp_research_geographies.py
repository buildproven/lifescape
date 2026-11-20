"""BUI-2016: fetch_live_evidence reads NOAA station geographies from
LIFESCAPE_RESEARCH_GEOGRAPHIES.

The station mapping is system configuration (never user evidence), so the MCP fetch tool
must use the same environment parse as the web app's ConnectorEvidenceProvider while it
keeps its own injected connectors. Connectors are stubs; no test reaches a network.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import Client

from lifescape.connectors import Connector
from lifescape.connectors.base import DataRequest, RawResponse, ValidationResult
from lifescape.mcp_server import create_server
from lifescape.models import (
    Confidence,
    ObservationRecord,
    PlaceRecord,
    SourceRecord,
    SourceTier,
)
from lifescape.research import DiscoveryLead, SearchBrief, create_packet
from lifescape.research_sources import (
    ConnectorEvidenceProvider,
    research_geographies_from_environment,
)

ENV = "LIFESCAPE_RESEARCH_GEOGRAPHIES"
NEW_BERN, LAKE_GENEVA = "3746340", "5541450"
SNOWFALL = "annual_snowfall"
STATION = "USW00013754:2024"
NOAA_URL = "https://www.ncei.noaa.gov/stub/gsoy"


class RecordingNoaa:
    """Stands in for noaa_gsoy and records each station-year it is asked for."""

    name = "noaa_gsoy"
    supported_metric_ids = (SNOWFALL,)

    def __init__(self, requests: list[DataRequest]) -> None:
        self.requests = requests

    def fetch(self, request: DataRequest) -> RawResponse:
        self.requests.append(request)
        return RawResponse(source_url=NOAA_URL, payload=b"{}", checksum="stub")

    def normalize(self, response: RawResponse) -> list[ObservationRecord]:
        del response
        today = date.today()
        return [
            ObservationRecord(
                place=PlaceRecord(place_id="station-native", name="Stub", state="ZZ"),
                metric_id=SNOWFALL,
                raw_value=2.0,
                observed_period="2024",
                observed_at=today - timedelta(days=30),
                source=SourceRecord(
                    url=NOAA_URL,
                    title="Stub GSOY snowfall",
                    publisher="NOAA NCEI",
                    tier=SourceTier.A,
                    retrieved_at=today,
                    geography="station",
                    confidence=Confidence.HIGH,
                    synthetic=False,
                ),
            )
        ]

    def validate(self, observations: list[ObservationRecord]) -> ValidationResult:
        del observations
        return ValidationResult(valid=True)


def _factory(requests: list[DataRequest]) -> Callable[[], Sequence[Connector]]:
    def build() -> Sequence[Connector]:
        return (RecordingNoaa(requests),)

    return build


def _fetch_snowfall(output_dir: Path, requests: list[DataRequest]) -> dict[str, Any]:
    async def main() -> dict[str, Any]:
        server = create_server(output_dir=output_dir, live_connectors=_factory(requests))
        async with Client(server, mode="legacy") as client:
            result = await client.call_tool(
                "fetch_live_evidence",
                {"place_ids": [NEW_BERN, LAKE_GENEVA], "metric_ids": [SNOWFALL]},
            )
        assert not result.is_error, result.content
        assert isinstance(result.structured_content, dict)
        return result.structured_content

    return anyio.run(main)


def test_bui_2016_fetch_live_evidence_uses_configured_noaa_station(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV, f'{{"{NEW_BERN}":{{"noaa_gsoy":"{STATION}"}}}}')
    requests: list[DataRequest] = []

    payload = _fetch_snowfall(tmp_path, requests)

    assert [request.geography for request in requests] == [STATION]
    assert [(item["place_id"], item["metric_id"]) for item in payload["observations"]] == [
        (NEW_BERN, SNOWFALL)
    ]
    assert payload["missing"] == [{"place_id": LAKE_GENEVA, "metric_id": SNOWFALL}]
    assert payload["errors"] == {
        LAKE_GENEVA: ["noaa_gsoy: explicit source geography is not configured"]
    }


def test_bui_2016_fetch_live_evidence_without_configuration_reports_station_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ENV, raising=False)
    requests: list[DataRequest] = []

    payload = _fetch_snowfall(tmp_path, requests)

    assert requests == []
    assert payload["observations"] == []
    assert set(payload["errors"]) == {NEW_BERN, LAKE_GENEVA}


def test_bui_2016_invalid_geography_configuration_fails_before_any_tool_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV, "not-json")

    with pytest.raises(ValueError, match=f"{ENV} must be valid JSON"):
        create_server(output_dir=tmp_path, live_connectors=_factory([]))


def test_bui_2016_environment_parse_is_separate_from_default_connectors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENV, f'{{"{NEW_BERN}":{{"noaa_gsoy":"{STATION}"}}}}')
    requests: list[DataRequest] = []

    assert research_geographies_from_environment() == {NEW_BERN: {"noaa_gsoy": STATION}}
    provider = ConnectorEvidenceProvider.from_environment(connectors=(RecordingNoaa(requests),))
    packet = create_packet(
        SearchBrief(preferences="A small coastal town with mild winters."),
        (
            DiscoveryLead(
                place=PlaceRecord(place_id=NEW_BERN, name="New Bern", state="NC"),
                rationale="Configured station fixture.",
            ),
        ),
    )

    result = provider.fetch(packet)

    assert [request.geography for request in requests] == [STATION]
    assert [item.metric_id for item in result.observations] == [SNOWFALL]
    assert result.errors == {}


def test_bui_2016_empty_configuration_yields_no_geographies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENV, "")

    assert research_geographies_from_environment() == {}
