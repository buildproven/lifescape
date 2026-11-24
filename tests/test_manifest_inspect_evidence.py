"""BUI-2167: the plugin manifest describes both evidence inputs of ``inspect_evidence``.

The tool reads a local evidence CSV (``evidence_path``) or a research packet's approved evidence
(``packet_id``, BUI-2018). The manifest description must name both, and the tool must still take
exactly those inputs, so the description and the server cannot drift apart again.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import anyio
from mcp import Client

from lifescape.mcp_server import create_server

REPOSITORY = Path(__file__).resolve().parents[1]


def _manifest_tool(name: str) -> dict[str, Any]:
    manifest = json.loads((REPOSITORY / "manifest.json").read_text("utf-8"))
    (entry,) = [tool for tool in manifest["tools"] if tool["name"] == name]
    assert isinstance(entry, dict)
    return entry


def _input_schema(output_dir: Path, name: str) -> dict[str, Any]:
    async def main() -> dict[str, Any]:
        server = create_server(output_dir=output_dir)
        async with Client(server, mode="legacy") as client:
            listed = await client.list_tools()
            (tool,) = [tool for tool in listed.tools if tool.name == name]
            return dict(tool.input_schema)

    return anyio.run(main)


def test_manifest_inspect_evidence_names_the_csv_and_the_packet_inputs() -> None:
    description = _manifest_tool("inspect_evidence")["description"].lower()

    assert "evidence csv" in description
    assert "packet" in description
    assert "approved" in description
    # The text the card replaces said a CSV was the only input.
    assert description != "check an evidence csv for validation errors and missing metrics"


def test_manifest_inspect_evidence_keeps_what_the_tool_checks() -> None:
    description = _manifest_tool("inspect_evidence")["description"].lower()

    assert description.startswith("check ")
    assert "validation errors" in description
    assert "missing metrics" in description


def test_manifest_inspect_evidence_matches_the_tool_inputs(tmp_path: Path) -> None:
    properties = _input_schema(tmp_path, "inspect_evidence")["properties"]

    assert {"evidence_path", "packet_id"} <= set(properties)
