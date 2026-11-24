"""BUI-2166 (REQ-PKT-4): the hosted server refuses a manifest_path, as it refuses evidence_path.

Under a database store (``LIFESCAPE_STORE=database``) a manifest path names a file on the server's
disk, not the caller's. explain_town and inspect_evidence must refuse it before reading anything.
The live connector is a stub, so no test reaches a network; its values are fixtures, not
real-world evidence.
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
from lifescape.store import Store
from lifescape.store_db import DatabaseStore

MANIFEST = Path(__file__).parent / "fixtures" / "mcp" / "new-bern-manifest.csv"
NEW_BERN = "3746340"
STUB_URL = "https://data.census.gov/stub/acs"
METRIC = "median_sale_price"
TOOLS = ("explain_town", "inspect_evidence")


class StubCensus:
    """Proposes one metric for the requested town."""

    name = "census_acs"
    supported_metric_ids = (METRIC,)

    def fetch(self, request: DataRequest) -> RawResponse:
        return RawResponse(source_url=STUB_URL, payload=request.geography.encode(), checksum="s")

    def normalize(self, response: RawResponse) -> list[ObservationRecord]:
        del response
        today = date.today()
        return [
            ObservationRecord(
                place=PlaceRecord(place_id="connector-native", name="Stub", state="ZZ"),
                metric_id=METRIC,
                raw_value=320000.0,
                observed_period="2020-2024",
                observed_at=today - timedelta(days=30),
                source=SourceRecord(
                    url=STUB_URL,
                    title="Stub ACS",
                    publisher="U.S. Census Bureau",
                    tier=SourceTier.A,
                    retrieved_at=today,
                    geography="town",
                    confidence=Confidence.HIGH,
                    synthetic=False,
                ),
            )
        ]

    def validate(self, observations: list[ObservationRecord]) -> ValidationResult:
        del observations
        return ValidationResult(valid=True)


def _connectors() -> Sequence[Connector]:
    return (StubCensus(),)


def _text(result: CallToolResult) -> str:
    return "\n".join(item.text for item in result.content if isinstance(item, TextContent))


def _with_packet(
    output_dir: Path, tool: str, manifest: str | None, store: Store | None
) -> CallToolResult:
    """Fetch a packet for New Bern, then call ``tool`` on it with ``manifest`` as manifest_path."""

    async def main() -> CallToolResult:
        server = create_server(output_dir=output_dir, live_connectors=_connectors, store=store)
        async with Client(server, mode="legacy") as client:
            fetched = await client.call_tool(
                "fetch_live_evidence", {"place_ids": [NEW_BERN], "metric_ids": [METRIC]}
            )
            assert not fetched.is_error, _text(fetched)
            assert isinstance(fetched.structured_content, dict)
            arguments: dict[str, Any] = {"packet_id": fetched.structured_content["packet_id"]}
            if tool == "explain_town":
                arguments["place_id"] = NEW_BERN
            if manifest is not None:
                arguments["manifest_path"] = manifest
            return await client.call_tool(tool, arguments)

    return anyio.run(main)


def _assert_refused(result: CallToolResult) -> None:
    assert result.is_error
    assert "manifest_path" in _text(result)
    assert "hosted server" in _text(result)


@pytest.mark.parametrize("tool", TOOLS)
def test_req_pkt_4_database_store_refuses_manifest_path(tmp_path: Path, tool: str) -> None:
    store = DatabaseStore.from_url("sqlite://")

    _assert_refused(_with_packet(tmp_path, tool, str(MANIFEST), store))


@pytest.mark.parametrize("tool", TOOLS)
def test_req_pkt_4_database_store_refuses_missing_manifest_path(tmp_path: Path, tool: str) -> None:
    # The refusal does not depend on the file: a missing path is refused, not probed.
    store = DatabaseStore.from_url("sqlite://")

    _assert_refused(_with_packet(tmp_path, tool, str(tmp_path / "absent.csv"), store))


def test_req_pkt_4_database_store_env_refuses_manifest_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LIFESCAPE_STORE", "database")
    monkeypatch.setenv("DATABASE_URL", "sqlite://")

    _assert_refused(_with_packet(tmp_path, "inspect_evidence", str(MANIFEST), None))


@pytest.mark.parametrize("tool", TOOLS)
def test_req_pkt_4_database_store_without_manifest_still_works(tmp_path: Path, tool: str) -> None:
    store = DatabaseStore.from_url("sqlite://")

    result = _with_packet(tmp_path, tool, None, store)

    assert not result.is_error, _text(result)
