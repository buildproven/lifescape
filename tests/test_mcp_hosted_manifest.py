"""BUI-2166: the hosted MCP server refuses manifest_path, as it refuses evidence_path.

On the database store (the hosted server), a manifest path names a file on the server's disk,
not the user's. explain_town and inspect_evidence must refuse it before they read any file, so
a caller cannot make the server read an arbitrary server file or learn whether one exists. The
stub connector proposes fixed fixture values, not real-world evidence, and reaches no network.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import Client
from mcp_types import CallToolResult, TextContent

from lifescape.connectors import Connector
from lifescape.connectors.base import DataRequest, RawResponse, ValidationResult
from lifescape.mcp_server import create_server
from lifescape.models import Confidence, ObservationRecord, PlaceRecord, SourceRecord, SourceTier
from lifescape.store_db import DatabaseStore

FIXTURES = Path(__file__).parent / "fixtures" / "mcp"
EVIDENCE = FIXTURES / "finalists-evidence.csv"
NEW_BERN_MANIFEST = FIXTURES / "new-bern-manifest.csv"
MANIFEST_TOOLS = ("explain_town", "inspect_evidence")
LAKE_GENEVA, NEW_BERN = "5541450", "3746340"
STUB_SOURCE_URL = "https://data.census.gov/stub/acs"
CRITICAL = (
    "median_sale_price",
    "er_drive_minutes",
    "broadband_mbps_down",
    "annual_snowfall",
    "flood_risk_score",
    "distress_index",
    "one_level_inventory_count",
)


class StubCensus:
    """Stands in for census_acs and proposes the value 1.0 for every critical metric."""

    name = "census_acs"
    supported_metric_ids = CRITICAL

    def fetch(self, request: DataRequest) -> RawResponse:
        return RawResponse(
            source_url=STUB_SOURCE_URL, payload=request.geography.encode(), checksum="stub"
        )

    def normalize(self, response: RawResponse) -> list[ObservationRecord]:
        del response
        today = date.today()
        return [
            ObservationRecord(
                place=PlaceRecord(place_id="connector-native", name="Stub", state="ZZ"),
                metric_id=metric_id,
                raw_value=1.0,
                observed_period="2020-2024",
                observed_at=today - timedelta(days=30),
                source=SourceRecord(
                    url=STUB_SOURCE_URL,
                    title=f"Stub ACS {metric_id}",
                    publisher="U.S. Census Bureau",
                    tier=SourceTier.A,
                    retrieved_at=today,
                    geography="town",
                    confidence=Confidence.HIGH,
                    synthetic=False,
                ),
            )
            for metric_id in CRITICAL
        ]

    def validate(self, observations: list[ObservationRecord]) -> ValidationResult:
        del observations
        return ValidationResult(valid=True)


def _connectors() -> Sequence[Connector]:
    return (StubCensus(),)


def _text(result: CallToolResult) -> str:
    return "\n".join(item.text for item in result.content if isinstance(item, TextContent))


def _arguments(tool: str) -> dict[str, Any]:
    return {"place_id": NEW_BERN} if tool == "explain_town" else {}


def _hosted_call(tmp_path: Path, tool: str, arguments: dict[str, Any]) -> CallToolResult:
    """Fetch a packet on a database store, then call ``tool`` with that packet's id."""

    async def main() -> CallToolResult:
        store = DatabaseStore.from_url("sqlite://")
        server = create_server(output_dir=tmp_path, live_connectors=_connectors, store=store)
        async with Client(server, mode="legacy") as client:
            fetched = await client.call_tool(
                "fetch_live_evidence",
                {"place_ids": [NEW_BERN, LAKE_GENEVA], "metric_ids": list(CRITICAL)},
            )
            assert not fetched.is_error, _text(fetched)
            assert isinstance(fetched.structured_content, dict)
            packet_id = fetched.structured_content["packet_id"]
            return await client.call_tool(tool, {"packet_id": packet_id, **arguments})

    return anyio.run(main)


@pytest.mark.parametrize("tool", MANIFEST_TOOLS)
def test_bui_2166_database_store_refuses_an_existing_manifest_path(
    tmp_path: Path, tool: str
) -> None:
    result = _hosted_call(
        tmp_path, tool, {**_arguments(tool), "manifest_path": str(NEW_BERN_MANIFEST)}
    )

    assert result.is_error
    text = _text(result)
    assert "manifest_path" in text
    assert "hosted server" in text


@pytest.mark.parametrize("tool", MANIFEST_TOOLS)
def test_bui_2166_database_store_does_not_reveal_whether_a_server_file_exists(
    tmp_path: Path, tool: str
) -> None:
    missing = tmp_path / "no-such-manifest.csv"

    result = _hosted_call(tmp_path, tool, {**_arguments(tool), "manifest_path": str(missing)})

    assert result.is_error
    text = _text(result)
    assert "hosted server" in text
    assert "not found" not in text
    assert str(missing) not in text


@pytest.mark.parametrize("tool", MANIFEST_TOOLS)
def test_bui_2166_database_store_without_manifest_path_still_reads_the_packet(
    tmp_path: Path, tool: str
) -> None:
    result = _hosted_call(tmp_path, tool, _arguments(tool))

    assert not result.is_error, _text(result)


def test_bui_2166_local_store_keeps_accepting_manifest_path(tmp_path: Path) -> None:
    async def main() -> CallToolResult:
        server = create_server(output_dir=tmp_path, live_connectors=_connectors)
        async with Client(server, mode="legacy") as client:
            return await client.call_tool(
                "inspect_evidence",
                {"evidence_path": str(EVIDENCE), "manifest_path": str(NEW_BERN_MANIFEST)},
            )

    result = anyio.run(main)

    assert not result.is_error, _text(result)


def test_bui_2166_database_store_from_env_refuses_manifest_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LIFESCAPE_STORE", "database")
    monkeypatch.setenv("DATABASE_URL", "sqlite://")

    async def main() -> CallToolResult:
        server = create_server(output_dir=tmp_path, live_connectors=_connectors)
        async with Client(server, mode="legacy") as client:
            return await client.call_tool(
                "inspect_evidence",
                {"packet_id": "0123456789ab", "manifest_path": str(NEW_BERN_MANIFEST)},
            )

    result = anyio.run(main)

    assert result.is_error
    assert "manifest_path" in _text(result)
