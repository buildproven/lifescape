"""BUI-1779: the local stdio MCP server over the discovery and evidence engines.

Every tool is called through an MCP client session (in-memory JSON-RPC streams, or a real
``lifescape mcp`` subprocess over stdio). The fixture evidence is synthetic.
"""

from __future__ import annotations

import shutil
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import anyio
import pytest
import yaml
from mcp import Client, StdioServerParameters
from mcp_types import CallToolResult, TextContent

from lifescape.mcp_server import create_server

FIXTURES = Path(__file__).parent / "fixtures" / "mcp"
EVIDENCE = FIXTURES / "finalists-evidence.csv"
NEW_BERN_MANIFEST = FIXTURES / "new-bern-manifest.csv"
CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"
LAKE_GENEVA, NEW_BERN, MARYVILLE = "5541450", "3746340", "4746380"
TOOLS = {
    "find_place",
    "discover_towns",
    "compare_finalists",
    "explain_town",
    "inspect_evidence",
    "fetch_live_evidence",
    "approve_evidence",
    "export_report",
}
GATE_STATES = {"PASS", "FAIL", "UNKNOWN", "WAIVED"}
CRITICAL_GATES = {
    "purchase_feasibility",
    "healthcare",
    "broadband",
    "winter_severity",
    "hazard_profile",
    "distress_profile",
    "aging_in_place_supply",
}


def _session[T](
    output_dir: Path,
    body: Callable[[Client], Awaitable[T]],
    *,
    config_dir: Path | None = None,
) -> T:
    async def main() -> T:
        server = create_server(output_dir=output_dir, config_dir=config_dir)
        async with Client(server, mode="legacy") as client:
            return await body(client)

    return anyio.run(main)


def _call(
    output_dir: Path,
    tool: str,
    arguments: dict[str, Any],
    *,
    config_dir: Path | None = None,
) -> CallToolResult:
    async def body(client: Client) -> CallToolResult:
        return await client.call_tool(tool, arguments)

    return _session(output_dir, body, config_dir=config_dir)


def _ok(result: CallToolResult) -> dict[str, Any]:
    assert not result.is_error, _text(result)
    assert isinstance(result.structured_content, dict)
    return result.structured_content


def _text(result: CallToolResult) -> str:
    return "\n".join(item.text for item in result.content if isinstance(item, TextContent))


# REQ-MCP-1 ---------------------------------------------------------------------------------


def test_req_mcp_1_lists_four_tools_with_strict_input_schemas(tmp_path: Path) -> None:
    async def body(client: Client) -> dict[str, dict[str, Any]]:
        listed = await client.list_tools()
        return {tool.name: tool.input_schema for tool in listed.tools}

    schemas = _session(tmp_path, body)

    assert set(schemas) == TOOLS
    for name, schema in schemas.items():
        assert schema["type"] == "object", name
        assert schema["additionalProperties"] is False, name
        assert schema["required"], name
    assert schemas["compare_finalists"]["properties"]["place_ids"]["minItems"] == 2
    assert set(schemas["compare_finalists"]["required"]) == {"place_ids", "evidence_path"}
    assert schemas["find_place"]["properties"]["query"]["minLength"] == 1
    profile_ref = schemas["discover_towns"]["properties"]["profile"]["$ref"].rsplit("/", 1)[-1]
    assert schemas["discover_towns"]["$defs"][profile_ref]["additionalProperties"] is False


def test_req_mcp_1_rejects_arguments_outside_the_schema(tmp_path: Path) -> None:
    unknown = _call(tmp_path, "find_place", {"query": "New Bern", "bogus": 1})
    assert unknown.is_error
    assert "bogus" in _text(unknown)

    missing = _call(tmp_path, "find_place", {})
    assert missing.is_error
    assert "query" in _text(missing)

    one_finalist = _call(
        tmp_path,
        "compare_finalists",
        {"place_ids": [NEW_BERN], "evidence_path": str(EVIDENCE)},
    )
    assert one_finalist.is_error

    nested = _call(
        tmp_path,
        "discover_towns",
        {"profile": {"exemplars": [NEW_BERN], "vibe": "coastal"}},
    )
    assert nested.is_error
    assert "vibe" in _text(nested)


def test_req_mcp_1_lifescape_mcp_serves_over_stdio(tmp_path: Path) -> None:
    async def main() -> set[str]:
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "lifescape.cli", "mcp", "--output-dir", str(tmp_path)],
        )
        with anyio.fail_after(60):
            async with Client(params) as client:
                listed = await client.list_tools()
                found = await client.call_tool("find_place", {"query": "New Bern, NC"})
                assert not found.is_error, _text(found)
                assert isinstance(found.structured_content, dict)
                assert found.structured_content["matches"][0]["place_id"] == NEW_BERN
                return {tool.name for tool in listed.tools}

    assert anyio.run(main) == TOOLS


# REQ-MCP-2 ---------------------------------------------------------------------------------


def test_req_mcp_2_find_place_returns_catalog_lookup_matches(tmp_path: Path) -> None:
    payload = _ok(_call(tmp_path, "find_place", {"query": "new bern, nc"}))

    assert payload["matches"][0] == {
        "place_id": NEW_BERN,
        "name": "New Bern",
        "state": "NC",
        "region": "South",
        "label": "New Bern, NC",
        "population": payload["matches"][0]["population"],
        "can_be_example": True,
    }
    assert payload["matches"][0]["population"] > 2500

    limited = _ok(_call(tmp_path, "find_place", {"query": "Spring", "limit": 3}))
    assert len(limited["matches"]) == 3

    nothing = _ok(_call(tmp_path, "find_place", {"query": "Zzyzxqqq"}))
    assert nothing["matches"] == []


def test_req_mcp_2_discover_towns_returns_matches_with_explanations(tmp_path: Path) -> None:
    profile = {
        "exemplars": [NEW_BERN],
        "priorities": {"freezing_nights_per_year": 5},
        "hard_constraints": [{"field": "median_home_value", "operator": "max", "value": 450000}],
        "include_states": ["NC", "SC", "VA"],
    }
    payload = _ok(_call(tmp_path, "discover_towns", {"profile": profile, "limit": 4}))

    matches = payload["matches"]
    assert [match["rank"] for match in matches] == [1, 2, 3, 4]
    assert NEW_BERN not in {match["place_id"] for match in matches}
    for match in matches:
        assert match["label"].rsplit(", ", 1)[1] in {"NC", "SC", "VA"}
        assert match["reasons"] and all(reason["text"] for reason in match["reasons"])
        assert match["trade_offs"] and all(item["text"] for item in match["trade_offs"])
        assert set(match["unknowns"]) == {"missing_fields", "unknown_constraints"}
    diagnostics = payload["diagnostics"]
    assert diagnostics["returned_count"] == 4
    exclusions = {item["constraint_id"] for item in diagnostics["known_constraint_exclusions"]}
    assert "median_home_value_max" in exclusions
    assert payload["profile"]["exemplars"][0]["place_id"] == NEW_BERN
    assert payload["evidence_status"] == "discovery leads; not verified evidence"


def test_req_mcp_2_discover_towns_reports_profile_errors(tmp_path: Path) -> None:
    result = _call(tmp_path, "discover_towns", {"profile": {"exemplars": ["no-such-town"]}})

    assert result.is_error
    assert "unknown exemplar town: no-such-town" in _text(result)


# REQ-MCP-3 ---------------------------------------------------------------------------------


def _compare(tmp_path: Path, place_ids: list[str], **extra: Any) -> CallToolResult:
    return _call(
        tmp_path,
        "compare_finalists",
        {"place_ids": place_ids, "evidence_path": str(EVIDENCE), "as_of": "2026-01-01", **extra},
    )


def test_req_mcp_3_blocks_finalist_with_unknown_critical_evidence(tmp_path: Path) -> None:
    payload = _ok(_compare(tmp_path, [MARYVILLE, LAKE_GENEVA, NEW_BERN]))

    finalists = {item["place_id"]: item for item in payload["finalists"]}
    assert set(finalists) == {LAKE_GENEVA, NEW_BERN, MARYVILLE}
    maryville = finalists[MARYVILLE]
    assert maryville["status"] == "blocked"
    assert maryville["rank"] is None
    assert maryville["total_score"] is None
    assert maryville["sensitivity"] is None
    assert maryville["blocked_by"] == [{"gate_id": "broadband", "result": "UNKNOWN"}]
    assert "broadband_mbps_down" in maryville["unknowns"]
    assert payload["blocked"] == [MARYVILLE]
    assert MARYVILLE not in {item["place_id"] for item in payload["ranking"]}

    ranked = [finalists[LAKE_GENEVA], finalists[NEW_BERN]]
    assert sorted(item["rank"] for item in ranked) == [1, 2]
    assert [item["rank"] for item in payload["ranking"]] == [1, 2]
    for item in ranked:
        assert item["status"] == "ranked"
        assert item["blocked_by"] == []
        assert item["sensitivity"]["place_id"] == item["place_id"]
        assert "broadband_mbps_down" not in item["unknowns"]
    for item in payload["finalists"]:
        assert {gate["gate_id"] for gate in item["gates"]} == CRITICAL_GATES
        assert {gate["result"] for gate in item["gates"]} <= GATE_STATES
    broadband = next(g for g in maryville["gates"] if g["gate_id"] == "broadband")
    assert broadband["metric_id"] == "broadband_mbps_down"
    assert broadband["raw_value"] is None

    assert payload["synthetic_evidence"] is True
    assert payload["evaluated_as_of"] == "2026-01-01"
    for key in ("markdown", "ranking_csv", "sensitivity_csv", "database"):
        assert Path(payload["reports"][key]).is_file(), key
    ranking_csv = Path(payload["reports"]["ranking_csv"]).read_text(encoding="utf-8")
    assert MARYVILLE not in ranking_csv


def test_req_mcp_3_repeated_comparison_returns_the_same_run(tmp_path: Path) -> None:
    first = _ok(_compare(tmp_path, [LAKE_GENEVA, NEW_BERN]))
    second = _ok(_compare(tmp_path, [NEW_BERN, LAKE_GENEVA]))

    assert second["run_id"] == first["run_id"]
    assert second["ranking"] == first["ranking"]
    assert second["reports"] == first["reports"]


def test_req_mcp_3_reports_an_impossible_as_of_date(tmp_path: Path) -> None:
    result = _compare(tmp_path, [LAKE_GENEVA, NEW_BERN], as_of="2026-13-45")

    assert result.is_error
    assert "month must be in 1..12" in _text(result)


def test_req_mcp_3_ranks_only_the_named_finalists(tmp_path: Path) -> None:
    payload = _ok(_compare(tmp_path, [LAKE_GENEVA, NEW_BERN]))

    assert {item["place_id"] for item in payload["finalists"]} == {LAKE_GENEVA, NEW_BERN}
    assert payload["blocked"] == []
    selected = Path(payload["reports"]["database"]).with_name("selected-evidence.csv")
    assert MARYVILLE not in selected.read_text(encoding="utf-8")


def test_req_mcp_3_uses_a_brief_that_is_not_benchmark_only(tmp_path: Path) -> None:
    # The packaged default brief is benchmark-only and rejects real evidence; a real row
    # must still compare because the tool runs on the live brief.
    real = tmp_path / "real.csv"
    rows = EVIDENCE.read_text(encoding="utf-8").replace(",true,", ",false,")
    real.write_text(rows, encoding="utf-8")

    payload = _ok(
        _call(
            tmp_path,
            "compare_finalists",
            {
                "place_ids": [LAKE_GENEVA, NEW_BERN],
                "evidence_path": str(real),
                "as_of": "2026-01-01",
            },
        )
    )

    assert payload["synthetic_evidence"] is False
    assert len(payload["ranking"]) == 2


def test_req_mcp_3_refuses_a_benchmark_only_live_brief(tmp_path: Path) -> None:
    config = tmp_path / "config"
    shutil.copytree(CONFIG_DIR, config)
    brief_path = config / "research_brief.live.yaml"
    brief = yaml.safe_load(brief_path.read_text(encoding="utf-8"))
    brief["benchmark_only"] = True
    brief_path.write_text(yaml.safe_dump(brief), encoding="utf-8")

    result = _call(
        tmp_path / "out",
        "compare_finalists",
        {"place_ids": [LAKE_GENEVA, NEW_BERN], "evidence_path": str(EVIDENCE)},
        config_dir=config,
    )

    assert result.is_error
    assert "benchmark-only" in _text(result)


@pytest.mark.parametrize(
    ("place_ids", "evidence", "message"),
    [
        ([LAKE_GENEVA, "9999999"], EVIDENCE, "no rows for place IDs: ['9999999']"),
        ([LAKE_GENEVA, LAKE_GENEVA], EVIDENCE, "place_ids must be distinct"),
        ([LAKE_GENEVA, NEW_BERN], FIXTURES / "missing.csv", "evidence CSV not found"),
    ],
)
def test_req_mcp_3_reports_unusable_requests(
    tmp_path: Path, place_ids: list[str], evidence: Path, message: str
) -> None:
    result = _call(
        tmp_path,
        "compare_finalists",
        {"place_ids": place_ids, "evidence_path": str(evidence)},
    )

    assert result.is_error
    assert message in _text(result)


@pytest.mark.parametrize(
    ("header", "message"),
    [
        ("town,state\n", "no place_id column"),
        ("place_id,place_id\n", "duplicate columns"),
    ],
)
def test_req_mcp_3_reports_malformed_evidence_headers(
    tmp_path: Path, header: str, message: str
) -> None:
    evidence = tmp_path / "bad.csv"
    evidence.write_text(header + f"{LAKE_GENEVA},{NEW_BERN}\n", encoding="utf-8")

    result = _call(
        tmp_path,
        "compare_finalists",
        {"place_ids": [LAKE_GENEVA, NEW_BERN], "evidence_path": str(evidence)},
    )

    assert result.is_error
    assert message in _text(result)


# REQ-MCP-4 ---------------------------------------------------------------------------------


def test_req_mcp_4_explains_town_with_research_card_and_next_actions(tmp_path: Path) -> None:
    payload = _ok(
        _call(
            tmp_path,
            "explain_town",
            {"place_id": MARYVILLE, "evidence_path": str(EVIDENCE), "as_of": "2026-01-01"},
        )
    )

    assert payload["place_id"] == MARYVILLE
    assert payload["town"] == "Maryville, TN"
    assert payload["bucket"] == "Insufficient evidence"
    assert payload["synthetic_evidence"] is True
    unresolved = payload["unresolved_critical_metrics"]
    assert "broadband_mbps_down" in unresolved
    assert payload["next_action"] == payload["next_actions"][0]["action"]
    assert [item["metric_id"] for item in payload["next_actions"]] == unresolved
    actions = {item["metric_id"]: item["action"] for item in payload["next_actions"]}
    assert actions["broadband_mbps_down"] == (
        "Record an address or town broadband availability source."
    )


def test_req_mcp_4_reports_a_decision_ready_town(tmp_path: Path) -> None:
    payload = _ok(
        _call(
            tmp_path,
            "explain_town",
            {
                "place_id": NEW_BERN,
                "evidence_path": str(EVIDENCE),
                "manifest_path": str(NEW_BERN_MANIFEST),
                "as_of": "2026-01-01",
            },
        )
    )

    assert payload["bucket"] == "Decision ready"
    assert payload["synthetic_evidence"] is True
    assert payload["unresolved_critical_metrics"] == []
    assert payload["next_actions"] == []


def test_req_mcp_4_reports_a_missing_manifest(tmp_path: Path) -> None:
    result = _call(
        tmp_path,
        "explain_town",
        {
            "place_id": NEW_BERN,
            "evidence_path": str(EVIDENCE),
            "manifest_path": str(FIXTURES / "missing-manifest.csv"),
        },
    )

    assert result.is_error
    assert "manifest not found" in _text(result)
