"""BUI-2166: the hosted database store refuses manifest_path in explain_town and inspect_evidence.

The hosted server has no owner files, so a manifest path would read an arbitrary server file. It
is refused like evidence_path (REQ-PKT-4); the local store still reads the owner's manifest.
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
NEW_BERN = "3746340"
REVIEWER = "Brett Stark"
STUB_SOURCE_URL = "https://data.census.gov/stub/acs"
VALUES = {
    "median_sale_price": 320000.0,
    "er_drive_minutes": 10.0,
    "broadband_mbps_down": 400.0,
    "annual_snowfall": 2.0,
    "flood_risk_score": 5.0,
    "distress_index": 3.0,
    "one_level_inventory_count": 40.0,
}
MANIFEST_TOOLS = ("explain_town", "inspect_evidence")


class StubCensus:
    """Stands in for census_acs and proposes every critical metric for New Bern."""

    name = "census_acs"
    supported_metric_ids = tuple(VALUES)

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
                raw_value=value,
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
            for metric_id, value in VALUES.items()
        ]

    def validate(self, observations: list[ObservationRecord]) -> ValidationResult:
        del observations
        return ValidationResult(valid=True)


def _connectors() -> Sequence[Connector]:
    return (StubCensus(),)


def _ok(result: CallToolResult) -> dict[str, Any]:
    assert not result.is_error, _text(result)
    assert isinstance(result.structured_content, dict)
    return result.structured_content


def _text(result: CallToolResult) -> str:
    return "\n".join(item.text for item in result.content if isinstance(item, TextContent))


def _arguments(tool: str) -> dict[str, Any]:
    return {"place_id": NEW_BERN} if tool == "explain_town" else {}


def _hosted_call_with_manifest(tmp_path: Path, tool: str, manifest: Path) -> CallToolResult:
    """Approve a New Bern packet on the database store, then call ``tool`` with the packet and
    a manifest path."""
    store = DatabaseStore.from_url("sqlite://")

    async def main() -> CallToolResult:
        server = create_server(output_dir=tmp_path, live_connectors=_connectors, store=store)
        async with Client(server, mode="legacy") as client:
            fetched = await client.call_tool(
                "fetch_live_evidence", {"place_ids": [NEW_BERN], "metric_ids": list(VALUES)}
            )
            packet_id = _ok(fetched)["packet_id"]
            for metric_id in VALUES:
                _ok(
                    await client.call_tool(
                        "approve_evidence",
                        {
                            "packet_id": packet_id,
                            "place_id": NEW_BERN,
                            "metric_id": metric_id,
                            "reviewer": REVIEWER,
                        },
                    )
                )
            return await client.call_tool(
                tool,
                {**_arguments(tool), "packet_id": packet_id, "manifest_path": str(manifest)},
            )

    return anyio.run(main)


@pytest.mark.parametrize("tool", MANIFEST_TOOLS)
def test_bui_2166_database_store_refuses_manifest_path(tmp_path: Path, tool: str) -> None:
    result = _hosted_call_with_manifest(tmp_path, tool, NEW_BERN_MANIFEST)

    assert result.is_error
    assert "hosted server cannot read manifest_path" in _text(result)


@pytest.mark.parametrize("tool", MANIFEST_TOOLS)
def test_bui_2166_database_store_refuses_before_touching_the_server_file(
    tmp_path: Path, tool: str
) -> None:
    # A missing file gets the hosted refusal, not "manifest not found": the server never
    # looks at its own disk for the path.
    result = _hosted_call_with_manifest(tmp_path, tool, tmp_path / "server-file.csv")

    assert result.is_error
    assert "hosted server cannot read manifest_path" in _text(result)
    assert "not found" not in _text(result)


@pytest.mark.parametrize("tool", MANIFEST_TOOLS)
def test_bui_2166_local_store_still_reads_the_owner_manifest(tmp_path: Path, tool: str) -> None:
    async def main() -> CallToolResult:
        server = create_server(output_dir=tmp_path / "mcp", live_connectors=_connectors)
        async with Client(server, mode="legacy") as client:
            return await client.call_tool(
                tool,
                {
                    **_arguments(tool),
                    "evidence_path": str(EVIDENCE),
                    "manifest_path": str(NEW_BERN_MANIFEST),
                },
            )

    _ok(anyio.run(main))
