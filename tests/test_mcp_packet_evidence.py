"""BUI-2018: hosted MCP tools read approved evidence from a research packet.

Every tool is called through an in-memory MCP client session. The live connector is a stub that
proposes fixed values for every critical metric, so no test reaches a network. The stub values
are test fixtures, not real-world evidence.
"""

from __future__ import annotations

import tempfile
from collections.abc import Awaitable, Callable, Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import Client
from mcp_types import CallToolResult, TextContent

from lifescape.config import load_metrics
from lifescape.connectors import Connector
from lifescape.connectors.base import DataRequest, RawResponse, ValidationResult
from lifescape.mcp_server import create_server
from lifescape.models import Confidence, ObservationRecord, PlaceRecord, SourceRecord, SourceTier
from lifescape.research import (
    ResearchError,
    export_approved_evidence,
    require_complete_packet_evidence,
)
from lifescape.research_workspace import ResearchWorkspace
from lifescape.resources import bundled_config
from lifescape.store import LocalStore, Store
from lifescape.store_db import DatabaseStore

FIXTURES = Path(__file__).parent / "fixtures" / "mcp"
EVIDENCE = FIXTURES / "finalists-evidence.csv"
LAKE_GENEVA, NEW_BERN = "5541450", "3746340"
REVIEWER = "Brett Stark"
STUB_SOURCE_URL = "https://data.census.gov/stub/acs"
EVIDENCE_TOOLS = ("compare_finalists", "explain_town", "inspect_evidence")
# Values that pass every critical gate in config/gates.default.yaml; the towns differ so the
# engine produces a strict ranking.
VALUES: dict[str, dict[str, float]] = {
    "37:46340": {
        "median_sale_price": 320000.0,
        "er_drive_minutes": 10.0,
        "broadband_mbps_down": 400.0,
        "annual_snowfall": 2.0,
        "flood_risk_score": 5.0,
        "distress_index": 3.0,
        "one_level_inventory_count": 40.0,
    },
    "55:41450": {
        "median_sale_price": 520000.0,
        "er_drive_minutes": 20.0,
        "broadband_mbps_down": 150.0,
        "annual_snowfall": 45.0,
        "flood_risk_score": 2.0,
        "distress_index": 6.0,
        "one_level_inventory_count": 8.0,
    },
}
CRITICAL = tuple(VALUES["37:46340"])


class StubCensus:
    """Stands in for census_acs and proposes every critical metric for the requested town."""

    name = "census_acs"
    supported_metric_ids = CRITICAL

    def fetch(self, request: DataRequest) -> RawResponse:
        return RawResponse(
            source_url=STUB_SOURCE_URL, payload=request.geography.encode(), checksum="stub"
        )

    def normalize(self, response: RawResponse) -> list[ObservationRecord]:
        today = date.today()
        values = VALUES[response.payload.decode()]
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
            for metric_id, value in values.items()
        ]

    def validate(self, observations: list[ObservationRecord]) -> ValidationResult:
        del observations
        return ValidationResult(valid=True)


def _connectors() -> Sequence[Connector]:
    return (StubCensus(),)


def _session[T](
    output_dir: Path, body: Callable[[Client], Awaitable[T]], *, store: Store | None = None
) -> T:
    async def main() -> T:
        server = create_server(output_dir=output_dir, live_connectors=_connectors, store=store)
        async with Client(server, mode="legacy") as client:
            return await body(client)

    return anyio.run(main)


def _call(
    output_dir: Path, tool: str, arguments: dict[str, Any], *, store: Store | None = None
) -> CallToolResult:
    async def body(client: Client) -> CallToolResult:
        return await client.call_tool(tool, arguments)

    return _session(output_dir, body, store=store)


def _ok(result: CallToolResult) -> dict[str, Any]:
    assert not result.is_error, _text(result)
    assert isinstance(result.structured_content, dict)
    return result.structured_content


def _text(result: CallToolResult) -> str:
    return "\n".join(item.text for item in result.content if isinstance(item, TextContent))


def _fetch(output_dir: Path) -> str:
    payload = _ok(
        _call(
            output_dir,
            "fetch_live_evidence",
            {"place_ids": [NEW_BERN, LAKE_GENEVA], "metric_ids": list(CRITICAL)},
        )
    )
    packet_id = payload["packet_id"]
    assert isinstance(packet_id, str)
    return packet_id


def _approve(output_dir: Path, packet_id: str, places: Sequence[str]) -> None:
    for place_id in places:
        for metric_id in CRITICAL:
            _ok(
                _call(
                    output_dir,
                    "approve_evidence",
                    {
                        "packet_id": packet_id,
                        "place_id": place_id,
                        "metric_id": metric_id,
                        "reviewer": REVIEWER,
                    },
                )
            )


def _approved_packet(output_dir: Path) -> str:
    packet_id = _fetch(output_dir)
    _approve(output_dir, packet_id, (NEW_BERN, LAKE_GENEVA))
    return packet_id


def _web_export(output_dir: Path, packet_id: str) -> Path:
    """The CSV the web app's evidence export writes for this packet."""
    stored = LocalStore(output_dir).get_packet(packet_id)
    assert stored is not None
    with bundled_config() as config:
        text = export_approved_evidence(stored.promotions, load_metrics(config))
    path = output_dir.parent / "exported" / f"{packet_id}-approved-evidence.csv"
    path.parent.mkdir(parents=True)
    path.write_text(text, encoding="utf-8")
    return path


def _ranking_view(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "ranking": payload["ranking"],
        "blocked": payload["blocked"],
        "finalists": [
            {key: item[key] for key in ("place_id", "status", "rank", "total_score", "gates")}
            for item in payload["finalists"]
        ],
    }


# REQ-PKT-1 ---------------------------------------------------------------------------------


def test_req_pkt_1_compare_by_packet_id_matches_the_web_export_csv(tmp_path: Path) -> None:
    output = tmp_path / "mcp"
    packet_id = _approved_packet(output)
    exported = _web_export(output, packet_id)
    arguments = {"place_ids": [LAKE_GENEVA, NEW_BERN]}

    by_packet = _ok(_call(output, "compare_finalists", {**arguments, "packet_id": packet_id}))
    by_file = _ok(_call(output, "compare_finalists", {**arguments, "evidence_path": str(exported)}))

    assert set(by_packet) == set(by_file)
    assert [item["place_id"] for item in by_packet["ranking"]] == [NEW_BERN, LAKE_GENEVA]
    assert by_packet["blocked"] == []
    assert _ranking_view(by_packet) == _ranking_view(by_file)
    assert by_packet["run_id"] == by_file["run_id"]
    for path in by_packet["reports"].values():
        assert Path(path).is_file()


def test_req_pkt_1_explain_and_inspect_by_packet_id_match_the_web_export_csv(
    tmp_path: Path,
) -> None:
    output = tmp_path / "mcp"
    packet_id = _approved_packet(output)
    exported = _web_export(output, packet_id)

    explained = _ok(_call(output, "explain_town", {"place_id": NEW_BERN, "packet_id": packet_id}))
    explained_file = _ok(
        _call(output, "explain_town", {"place_id": NEW_BERN, "evidence_path": str(exported)})
    )
    assert explained == explained_file

    inspected = _ok(_call(output, "inspect_evidence", {"packet_id": packet_id}))
    inspected_file = _ok(_call(output, "inspect_evidence", {"evidence_path": str(exported)}))
    assert inspected["packet_id"] == packet_id
    assert inspected["evidence_path"] is None
    assert inspected_file["packet_id"] is None
    assert inspected_file["evidence_path"] == str(exported)
    assert set(inspected) == set(inspected_file)
    for key in ("valid", "errors", "as_of", "observation_count", "finding_count", "towns"):
        assert inspected[key] == inspected_file[key], key
    assert inspected["observation_count"] == 2 * len(CRITICAL)


def test_req_pkt_1_packet_calls_leave_no_evidence_file_and_change_no_packet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "mcp"
    packet_id = _approved_packet(output)
    system_temp = tmp_path / "system-temp"
    system_temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(system_temp))
    before = ResearchWorkspace(output / "research-workspace").load()
    files_before = {path for path in output.rglob("*") if path.is_file()}
    path_open = Path.open

    def refuse_evidence_file(path: Path, *args: Any, **kwargs: Any) -> Any:
        if path.suffix == ".csv" and "evidence" in path.name:
            raise AssertionError(f"packet call opened an evidence file: {path}")
        return path_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", refuse_evidence_file)

    _ok(_call(output, "inspect_evidence", {"packet_id": packet_id}))
    _ok(_call(output, "explain_town", {"place_id": NEW_BERN, "packet_id": packet_id}))
    compare = {"place_ids": [NEW_BERN, LAKE_GENEVA], "packet_id": packet_id}
    _ok(_call(output, "compare_finalists", compare))

    assert list(system_temp.iterdir()) == []
    assert ResearchWorkspace(output / "research-workspace").load() == before
    new_files = {path for path in output.rglob("*") if path.is_file()} - files_before
    assert not [path for path in new_files if path.name.endswith("approved-evidence.csv")]
    assert not [path for path in tmp_path.rglob("*approved-evidence.csv")]


def test_req_pkt_1_schemas_accept_packet_id_and_make_evidence_path_optional(
    tmp_path: Path,
) -> None:
    async def body(client: Client) -> dict[str, dict[str, Any]]:
        listed = await client.list_tools()
        return {tool.name: tool.input_schema for tool in listed.tools}

    schemas = _session(tmp_path, body)

    for name in EVIDENCE_TOOLS:
        properties = schemas[name]["properties"]
        assert "packet_id" in properties, name
        assert "evidence_path" in properties, name
        assert "packet_id" not in schemas[name].get("required", []), name
        assert "evidence_path" not in schemas[name].get("required", []), name
        assert schemas[name]["additionalProperties"] is False, name
    bad_id = _call(
        tmp_path, "compare_finalists", {"place_ids": [LAKE_GENEVA, NEW_BERN], "packet_id": "x"}
    )
    assert bad_id.is_error


# REQ-PKT-2 ---------------------------------------------------------------------------------


def _tool_arguments(tool: str) -> dict[str, Any]:
    if tool == "compare_finalists":
        return {"place_ids": [LAKE_GENEVA, NEW_BERN]}
    if tool == "explain_town":
        return {"place_id": NEW_BERN}
    return {}


@pytest.mark.parametrize("tool", EVIDENCE_TOOLS)
def test_req_pkt_2_refuses_both_evidence_path_and_packet_id(tmp_path: Path, tool: str) -> None:
    result = _call(
        tmp_path,
        tool,
        {**_tool_arguments(tool), "evidence_path": str(EVIDENCE), "packet_id": "0123456789ab"},
    )

    assert result.is_error
    assert "evidence_path" in _text(result)
    assert "packet_id" in _text(result)


@pytest.mark.parametrize("tool", EVIDENCE_TOOLS)
def test_req_pkt_2_refuses_neither_evidence_path_nor_packet_id(tmp_path: Path, tool: str) -> None:
    result = _call(tmp_path, tool, _tool_arguments(tool))

    assert result.is_error
    assert "evidence_path" in _text(result)
    assert "packet_id" in _text(result)


# REQ-PKT-3 ---------------------------------------------------------------------------------


@pytest.mark.parametrize("tool", EVIDENCE_TOOLS)
def test_req_pkt_3_unknown_packet_names_the_packet_id(tmp_path: Path, tool: str) -> None:
    result = _call(tmp_path, tool, {**_tool_arguments(tool), "packet_id": "0123456789ab"})

    assert result.is_error
    assert "research packet 0123456789ab not found" in _text(result)


@pytest.mark.parametrize("tool", EVIDENCE_TOOLS)
def test_req_pkt_3_incomplete_packet_reports_the_completeness_message(
    tmp_path: Path, tool: str
) -> None:
    output = tmp_path / "mcp"
    packet_id = _fetch(output)
    _approve(output, packet_id, (NEW_BERN,))
    stored = LocalStore(output).get_packet(packet_id)
    assert stored is not None

    with bundled_config() as config, pytest.raises(ResearchError) as expected:
        require_complete_packet_evidence(stored.packet, load_metrics(config), stored.promotions)

    result = _call(output, tool, {**_tool_arguments(tool), "packet_id": packet_id})

    assert result.is_error
    assert str(expected.value) in _text(result)
    assert LAKE_GENEVA in _text(result)


def test_req_pkt_3_packet_without_approvals_is_incomplete(tmp_path: Path) -> None:
    output = tmp_path / "mcp"
    packet_id = _fetch(output)

    result = _call(output, "inspect_evidence", {"packet_id": packet_id})

    assert result.is_error
    assert "critical evidence remains unapproved" in _text(result)


# REQ-PKT-4 ---------------------------------------------------------------------------------


@pytest.mark.parametrize("tool", EVIDENCE_TOOLS)
def test_req_pkt_4_database_store_refuses_evidence_path(tmp_path: Path, tool: str) -> None:
    store = DatabaseStore.from_url("sqlite://")

    result = _call(
        tmp_path, tool, {**_tool_arguments(tool), "evidence_path": str(EVIDENCE)}, store=store
    )

    assert result.is_error
    assert "packet_id" in _text(result)
    assert "hosted server" in _text(result)


def test_req_pkt_4_database_store_env_refuses_evidence_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LIFESCAPE_STORE", "database")
    monkeypatch.setenv("DATABASE_URL", "sqlite://")

    result = _call(
        tmp_path,
        "compare_finalists",
        {"place_ids": [LAKE_GENEVA, NEW_BERN], "evidence_path": str(EVIDENCE)},
    )

    assert result.is_error
    assert "pass packet_id" in _text(result)
    assert "hosted server" in _text(result)


def test_req_pkt_4_database_store_reads_a_packet(tmp_path: Path) -> None:
    store = DatabaseStore.from_url("sqlite://")

    async def body(client: Client) -> tuple[dict[str, Any], CallToolResult]:
        fetched = await client.call_tool(
            "fetch_live_evidence",
            {"place_ids": [NEW_BERN, LAKE_GENEVA], "metric_ids": list(CRITICAL)},
        )
        packet_id = _ok(fetched)["packet_id"]
        for place_id in (NEW_BERN, LAKE_GENEVA):
            for metric_id in CRITICAL:
                _ok(
                    await client.call_tool(
                        "approve_evidence",
                        {
                            "packet_id": packet_id,
                            "place_id": place_id,
                            "metric_id": metric_id,
                            "reviewer": REVIEWER,
                        },
                    )
                )
        compared = await client.call_tool(
            "compare_finalists", {"place_ids": [LAKE_GENEVA, NEW_BERN], "packet_id": packet_id}
        )
        return _ok(compared), await client.call_tool("inspect_evidence", {"packet_id": packet_id})

    compared, inspected = _session(tmp_path, body, store=store)

    assert [item["place_id"] for item in compared["ranking"]] == [NEW_BERN, LAKE_GENEVA]
    assert _ok(inspected)["valid"] is True


def test_req_pkt_4_local_store_keeps_accepting_evidence_path(tmp_path: Path) -> None:
    result = _call(tmp_path, "inspect_evidence", {"evidence_path": str(EVIDENCE)})

    payload = _ok(result)
    assert payload["evidence_path"] == str(EVIDENCE)
    assert payload["packet_id"] is None


# REQ-PKT-5 ---------------------------------------------------------------------------------


def test_req_pkt_5_skill_passes_packet_id_after_approve_evidence() -> None:
    skill = (Path(__file__).resolve().parents[1] / "skills" / "lifescape" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    workflow = skill.split("## Workflow", 1)[1].split("## Tools", 1)[0]
    steps = {
        number: workflow.split(f"{number}. **", 1)[1].split(f"{number + 1}. **", 1)[0]
        for number in (3, 4)
    }
    steps[5] = workflow.split("5. **", 1)[1]

    for number in (3, 5):
        assert "packet_id" in steps[number], number
        assert "evidence CSV" not in steps[number], number
    assert "packet_id" in steps[4]
    assert "approve_evidence" in steps[4]
    assert "never a file path" in workflow
    assert "evidence CSV that the server\ncan open" not in skill
