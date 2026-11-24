"""BUI-2166: the hosted MCP server refuses manifest_path, as it refuses evidence_path.

REQ-PKT-6: When explain_town or inspect_evidence runs on the hosted server (a database store)
with a manifest_path, the server shall refuse the call without reading the file, and name
manifest_path and the hosted server in the error. The local server keeps reading it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
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

FIXTURES = Path(__file__).parent / "fixtures" / "mcp"
EVIDENCE = FIXTURES / "finalists-evidence.csv"
MANIFEST = FIXTURES / "new-bern-manifest.csv"
NEW_BERN = "3746340"
REVIEWER = "Brett Stark"
STUB_SOURCE_URL = "https://data.census.gov/stub/acs"
MANIFEST_TOOLS = ("explain_town", "inspect_evidence")
VALUES: dict[str, float] = {
    "median_sale_price": 320000.0,
    "er_drive_minutes": 10.0,
    "broadband_mbps_down": 400.0,
    "annual_snowfall": 2.0,
    "flood_risk_score": 5.0,
    "distress_index": 3.0,
    "one_level_inventory_count": 40.0,
}
CRITICAL = tuple(VALUES)


class StubCensus:
    """Stands in for census_acs and proposes every critical metric for New Bern."""

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


def _session[T](output_dir: Path, body: Callable[[Client], Awaitable[T]], store: Store) -> T:
    async def main() -> T:
        server = create_server(output_dir=output_dir, live_connectors=_connectors, store=store)
        async with Client(server, mode="legacy") as client:
            return await body(client)

    return anyio.run(main)


def _ok(result: CallToolResult) -> dict[str, Any]:
    assert not result.is_error, _text(result)
    assert isinstance(result.structured_content, dict)
    return result.structured_content


def _text(result: CallToolResult) -> str:
    return "\n".join(item.text for item in result.content if isinstance(item, TextContent))


def _place_arguments(tool: str) -> dict[str, Any]:
    return {"place_id": NEW_BERN} if tool == "explain_town" else {}


def _call_with_approved_packet(
    output_dir: Path, tool: str, extra: dict[str, Any], store: Store
) -> tuple[CallToolResult, CallToolResult]:
    """Approve a New Bern packet, then call ``tool`` with it and with it plus ``extra``."""

    async def body(client: Client) -> tuple[CallToolResult, CallToolResult]:
        fetched = await client.call_tool(
            "fetch_live_evidence", {"place_ids": [NEW_BERN], "metric_ids": list(CRITICAL)}
        )
        packet_id = _ok(fetched)["packet_id"]
        for metric_id in CRITICAL:
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
        arguments = {**_place_arguments(tool), "packet_id": packet_id}
        plain = await client.call_tool(tool, arguments)
        return plain, await client.call_tool(tool, {**arguments, **extra})

    return _session(output_dir, body, store)


@pytest.mark.parametrize("tool", MANIFEST_TOOLS)
def test_req_pkt_6_database_store_refuses_an_existing_manifest_path(
    tmp_path: Path, tool: str
) -> None:
    plain, with_manifest = _call_with_approved_packet(
        tmp_path, tool, {"manifest_path": str(MANIFEST)}, DatabaseStore.from_url("sqlite://")
    )

    _ok(plain)
    assert with_manifest.is_error
    message = _text(with_manifest)
    assert "manifest_path" in message
    assert "hosted server" in message


@pytest.mark.parametrize("tool", MANIFEST_TOOLS)
def test_req_pkt_6_database_store_refusal_does_not_reveal_whether_the_file_exists(
    tmp_path: Path, tool: str
) -> None:
    _, present = _call_with_approved_packet(
        tmp_path / "present",
        tool,
        {"manifest_path": str(MANIFEST)},
        DatabaseStore.from_url("sqlite://"),
    )
    _, missing = _call_with_approved_packet(
        tmp_path / "missing",
        tool,
        {"manifest_path": str(tmp_path / "absent.csv")},
        DatabaseStore.from_url("sqlite://"),
    )

    assert present.is_error and missing.is_error
    assert _text(present) == _text(missing)
    assert "absent.csv" not in _text(missing)


@pytest.mark.parametrize("tool", MANIFEST_TOOLS)
def test_req_pkt_6_database_store_refuses_manifest_path_before_the_packet_lookup(
    tmp_path: Path, tool: str
) -> None:
    async def body(client: Client) -> CallToolResult:
        return await client.call_tool(
            tool,
            {
                **_place_arguments(tool),
                "packet_id": "0123456789ab",
                "manifest_path": str(MANIFEST),
            },
        )

    result = _session(tmp_path, body, DatabaseStore.from_url("sqlite://"))

    assert result.is_error
    assert "manifest_path" in _text(result)
    assert "hosted server" in _text(result)


def test_req_pkt_6_database_store_env_refuses_manifest_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LIFESCAPE_STORE", "database")
    monkeypatch.setenv("DATABASE_URL", "sqlite://")

    async def main() -> CallToolResult:
        server = create_server(output_dir=tmp_path, live_connectors=_connectors)
        async with Client(server, mode="legacy") as client:
            return await client.call_tool(
                "inspect_evidence",
                {"packet_id": "0123456789ab", "manifest_path": str(MANIFEST)},
            )

    result = anyio.run(main)

    assert result.is_error
    assert "manifest_path" in _text(result)
    assert "hosted server" in _text(result)
