"""REQ-MCP-5: run_tool, one stable door to every tool.

Claude keeps a connector's tool list from the start of a chat, so a tool added in a later release
is missing from an open chat. ``run_tool`` lists every tool the server has now and runs any of them
by name, with the arguments checked against the tool's current schema. The fixture evidence is
synthetic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp import Client
from test_mcp_server import TOOLS, _call, _ok, _session, _text


def test_req_mcp_5_lists_every_tool_with_its_arguments(tmp_path: Path) -> None:
    listed = _ok(_call(tmp_path, "run_tool", {}))["tools"]

    assert {item["name"] for item in listed} == TOOLS
    by_name = {item["name"]: item for item in listed}
    assert by_name["find_place"]["arguments"][0] == "query*"
    assert (
        "compare_finalists" in by_name and "place_ids*" in by_name["compare_finalists"]["arguments"]
    )
    assert by_name["run_tool"]["arguments"] == ["tool", "arguments"]
    for item in listed:
        assert item["summary"], item["name"]


def test_req_mcp_5_runs_a_tool_by_name_and_returns_its_own_result(tmp_path: Path) -> None:
    direct = _ok(_call(tmp_path, "find_place", {"query": "New Bern"}))
    through = _ok(
        _call(tmp_path, "run_tool", {"tool": "find_place", "arguments": {"query": "New Bern"}})
    )

    assert through == direct


def test_req_mcp_5_checks_arguments_against_the_tool_schema(tmp_path: Path) -> None:
    unknown = _call(
        tmp_path, "run_tool", {"tool": "find_place", "arguments": {"query": "x", "bogus": 1}}
    )
    assert unknown.is_error
    assert "arguments.bogus" in _text(unknown)

    missing = _call(tmp_path, "run_tool", {"tool": "find_place", "arguments": {}})
    assert missing.is_error
    assert "arguments.query" in _text(missing)


def test_req_mcp_5_refuses_an_unknown_tool_and_names_the_tools(tmp_path: Path) -> None:
    result = _call(tmp_path, "run_tool", {"tool": "no_such_tool"})

    assert result.is_error
    assert "compare_finalists" in _text(result)


def test_req_mcp_5_publishes_an_open_arguments_object_and_tells_the_agent(tmp_path: Path) -> None:
    async def body(client: Client) -> tuple[dict[str, Any], str]:
        listed = await client.list_tools()
        door = next(tool for tool in listed.tools if tool.name == "run_tool")
        return door.input_schema, door.description or ""

    schema, description = _session(tmp_path, body)

    options = schema["properties"]["arguments"]["anyOf"]
    assert any(option.get("type") == "object" for option in options)
    assert all(option.get("additionalProperties") is not False for option in options)
    assert "new chat" in description
