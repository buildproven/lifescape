"""BUI-2167: the plugin manifest says inspect_evidence also reads a packet's approved evidence.

``inspect_evidence`` takes a local evidence CSV (``evidence_path``) or a research packet's
approved values (``packet_id``). The manifest description shown to users must name both
inputs, not only "an evidence CSV".
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

from lifescape import mcp_server

REPOSITORY = Path(__file__).resolve().parents[1]


def _entry() -> dict[str, Any]:
    manifest = json.loads((REPOSITORY / "manifest.json").read_text("utf-8"))
    (entry,) = [tool for tool in manifest["tools"] if tool["name"] == "inspect_evidence"]
    return dict(entry)


def test_manifest_inspect_evidence_names_the_research_packet() -> None:
    description = _entry()["description"].lower()

    assert "evidence csv" in description
    assert "packet" in description
    assert "approved" in description


def test_manifest_inspect_evidence_still_reports_errors_and_missing_metrics() -> None:
    description = _entry()["description"].lower()

    assert "validation errors" in description
    assert "missing metrics" in description


def test_inspect_evidence_tool_takes_a_packet_as_the_manifest_says() -> None:
    source = inspect.getsource(mcp_server.create_server)

    assert "def inspect_evidence(" in source
    signature = source.split("def inspect_evidence(", 1)[1].split(") -> ", 1)[0]
    assert "evidence_path" in signature
    assert "packet_id" in signature
