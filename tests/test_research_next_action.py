"""BUI-1850: research next-action text is a public helper used by the MCP server."""

import ast
from pathlib import Path

import pytest

from lifescape import research_report
from lifescape.research_report import next_action_for

MCP_SERVER = Path(research_report.__file__).with_name("mcp_server.py")


def test_bui_1850_private_next_action_name_is_gone() -> None:
    assert not hasattr(research_report, "_next_action")


def test_bui_1850_mcp_server_imports_no_private_research_report_names() -> None:
    tree = ast.parse(MCP_SERVER.read_text(encoding="utf-8"))
    imported = [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "lifescape.research_report"
        for alias in node.names
    ]
    assert "next_action_for" in imported
    assert [name for name in imported if name.startswith("_")] == []


def test_bui_1850_no_metric_has_no_next_action() -> None:
    assert next_action_for(None) is None


@pytest.mark.parametrize(
    ("metric_id", "expected"),
    [
        ("median_sale_price", "Record a metric-correct median sale-price source and date."),
        ("er_drive_minutes", "Verify route time to an emergency-services hospital."),
        ("broadband_mbps_down", "Record an address or town broadband availability source."),
        ("annual_snowfall", "Record a station/year snowfall observation and source."),
        (
            "flood_risk_score",
            "Review flood exposure for finalist locations before property selection.",
        ),
        ("distress_index", "Record the configured Census-derived distress observation."),
        (
            "one_level_inventory_count",
            "Run and record the repeatable one-level listing search.",
        ),
    ],
)
def test_bui_1850_known_metric_next_action_text_is_unchanged(metric_id: str, expected: str) -> None:
    assert next_action_for(metric_id) == expected


def test_bui_1850_unknown_metric_gets_generic_next_action() -> None:
    assert next_action_for("walk_score") == "Record a metric-correct walk_score source and date."


def test_bui_1850_empty_metric_id_gets_generic_next_action() -> None:
    assert next_action_for("") == "Record a metric-correct  source and date."
