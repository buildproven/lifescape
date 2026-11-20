"""BUI-2031: packet inspection reports open critical values; comparison still requires none.

REQ-GAP-1: When ``inspect_evidence`` is called with a ``packet_id`` whose packet lacks approved
values for some critical metrics, it returns the approved values it has and lists each town's
missing critical metrics by ID, without raising.
REQ-GAP-2: When ``explain_town`` is called with a ``packet_id`` for a town with missing critical
metrics, it returns the town's explanation with those metrics unresolved (unknown), without
raising.
REQ-GAP-3: When ``compare_finalists`` is called with an incomplete packet, it still raises the
``require_complete_packet_evidence`` error.
REQ-GAP-4: SKILL.md step 3 says these tools report open critical values for an incomplete packet.

The live connector is the BUI-2018 stub; its values are test fixtures, not real-world evidence.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_mcp_packet_evidence import (
    CRITICAL,
    LAKE_GENEVA,
    NEW_BERN,
    REVIEWER,
    _call,
    _fetch,
    _ok,
    _text,
)

from lifescape.config import load_metrics
from lifescape.research import ResearchError, require_complete_packet_evidence
from lifescape.resources import bundled_config
from lifescape.store import LocalStore

SKILL = Path(__file__).resolve().parents[1] / "skills" / "lifescape" / "SKILL.md"
# New Bern gets every critical value but these; Lake Geneva gets none.
OPEN_FOR_NEW_BERN = ("annual_snowfall", "one_level_inventory_count")


def _approve_some(output: Path, packet_id: str) -> None:
    for metric_id in CRITICAL:
        if metric_id in OPEN_FOR_NEW_BERN:
            continue
        _ok(
            _call(
                output,
                "approve_evidence",
                {
                    "packet_id": packet_id,
                    "place_id": NEW_BERN,
                    "metric_id": metric_id,
                    "reviewer": REVIEWER,
                },
            )
        )


def _incomplete_packet(output: Path) -> str:
    packet_id = _fetch(output)
    _approve_some(output, packet_id)
    return packet_id


# REQ-GAP-1 ---------------------------------------------------------------------------------


def test_req_gap_1_inspect_lists_each_towns_open_critical_metrics(tmp_path: Path) -> None:
    output = tmp_path / "mcp"
    packet_id = _incomplete_packet(output)

    payload = _ok(_call(output, "inspect_evidence", {"packet_id": packet_id}))

    assert payload["packet_id"] == packet_id
    assert payload["critical_metrics_needed"] == {
        NEW_BERN: list(OPEN_FOR_NEW_BERN),
        LAKE_GENEVA: list(CRITICAL),
    }
    approved = len(CRITICAL) - len(OPEN_FOR_NEW_BERN)
    assert payload["observation_count"] == approved
    (town,) = payload["towns"]
    assert town["place_id"] == NEW_BERN
    assert {item["metric_id"] for item in town["metrics"]} == set(CRITICAL) - set(OPEN_FOR_NEW_BERN)
    assert set(OPEN_FOR_NEW_BERN) <= set(town["missing_metrics"])


def test_req_gap_1_inspect_packet_without_approvals_lists_every_critical_metric(
    tmp_path: Path,
) -> None:
    output = tmp_path / "mcp"
    packet_id = _fetch(output)

    payload = _ok(_call(output, "inspect_evidence", {"packet_id": packet_id}))

    assert payload["critical_metrics_needed"] == {
        NEW_BERN: list(CRITICAL),
        LAKE_GENEVA: list(CRITICAL),
    }
    assert payload["observation_count"] == 0
    assert payload["towns"] == []


def test_req_gap_1_inspect_complete_packet_needs_nothing(tmp_path: Path) -> None:
    output = tmp_path / "mcp"
    packet_id = _incomplete_packet(output)
    for place_id, metrics in ((NEW_BERN, OPEN_FOR_NEW_BERN), (LAKE_GENEVA, CRITICAL)):
        for metric_id in metrics:
            _ok(
                _call(
                    output,
                    "approve_evidence",
                    {
                        "packet_id": packet_id,
                        "place_id": place_id,
                        "metric_id": metric_id,
                        "reviewer": REVIEWER,
                    },
                )
            )

    payload = _ok(_call(output, "inspect_evidence", {"packet_id": packet_id}))

    assert payload["critical_metrics_needed"] == {NEW_BERN: [], LAKE_GENEVA: []}


def test_req_gap_1_inspect_evidence_path_has_no_packet_gap_list(tmp_path: Path) -> None:
    evidence = Path(__file__).parent / "fixtures" / "mcp" / "finalists-evidence.csv"

    payload = _ok(_call(tmp_path, "inspect_evidence", {"evidence_path": str(evidence)}))

    assert payload["critical_metrics_needed"] is None


# REQ-GAP-2 ---------------------------------------------------------------------------------


def test_req_gap_2_explain_town_with_some_approvals_marks_the_rest_unknown(
    tmp_path: Path,
) -> None:
    output = tmp_path / "mcp"
    packet_id = _incomplete_packet(output)

    payload = _ok(_call(output, "explain_town", {"place_id": NEW_BERN, "packet_id": packet_id}))

    assert payload["place_id"] == NEW_BERN
    assert payload["bucket"] == "Insufficient evidence"
    assert set(OPEN_FOR_NEW_BERN) <= set(payload["unresolved_critical_metrics"])
    assert [item["metric_id"] for item in payload["next_actions"]] == payload[
        "unresolved_critical_metrics"
    ]
    assert payload["synthetic_evidence"] is False


def test_req_gap_2_explain_town_without_approvals_marks_every_critical_metric_unknown(
    tmp_path: Path,
) -> None:
    output = tmp_path / "mcp"
    packet_id = _incomplete_packet(output)

    payload = _ok(_call(output, "explain_town", {"place_id": LAKE_GENEVA, "packet_id": packet_id}))

    assert payload["place_id"] == LAKE_GENEVA
    assert payload["town"].startswith("Lake Geneva")
    assert payload["bucket"] == "Insufficient evidence"
    assert payload["unresolved_critical_metrics"] == list(CRITICAL)
    assert [item["metric_id"] for item in payload["next_actions"]] == list(CRITICAL)
    assert payload["next_action"] == payload["next_actions"][0]["action"]
    assert payload["source_url"] is None
    assert payload["synthetic_evidence"] is False


def test_req_gap_2_explain_town_outside_the_packet_is_refused(tmp_path: Path) -> None:
    output = tmp_path / "mcp"
    packet_id = _incomplete_packet(output)

    result = _call(output, "explain_town", {"place_id": "0000000", "packet_id": packet_id})

    assert result.is_error
    assert "0000000" in _text(result)


# REQ-GAP-3 ---------------------------------------------------------------------------------


@pytest.mark.parametrize("approve", [True, False], ids=["some-approved", "none-approved"])
def test_req_gap_3_compare_finalists_still_refuses_an_incomplete_packet(
    tmp_path: Path, approve: bool
) -> None:
    output = tmp_path / "mcp"
    packet_id = _incomplete_packet(output) if approve else _fetch(output)
    stored = LocalStore(output).get_packet(packet_id)
    assert stored is not None
    with bundled_config() as config, pytest.raises(ResearchError) as expected:
        require_complete_packet_evidence(stored.packet, load_metrics(config), stored.promotions)

    result = _call(
        output,
        "compare_finalists",
        {"place_ids": [NEW_BERN, LAKE_GENEVA], "packet_id": packet_id},
    )

    assert result.is_error
    assert str(expected.value) in _text(result)


# REQ-GAP-4 ---------------------------------------------------------------------------------


def test_req_gap_4_skill_step_3_names_the_open_critical_value_report() -> None:
    skill = SKILL.read_text(encoding="utf-8")
    workflow = skill.split("## Workflow", 1)[1].split("## Tools", 1)[0]
    step_3 = " ".join(workflow.split("3. **", 1)[1].split("4. **", 1)[0].split())

    assert "explain_town" in step_3
    assert "inspect_evidence" in step_3
    assert "open critical values" in step_3
    assert "incomplete packet" in step_3
    assert "The tools read the packet only when every town" not in skill
