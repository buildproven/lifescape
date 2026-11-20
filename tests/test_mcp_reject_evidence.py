"""BUI-2017: the MCP ``reject_evidence`` tool records a named reviewer's rejection.

REQ-REJECT-1: When ``reject_evidence`` is called with a packet ID, a fetched value's place and
metric, a named reviewer, and a reason, it records the rejection through
``research.reject_evidence``, and a later ``approve_evidence`` of that value is refused.
REQ-REJECT-2: ``manifest.json`` and the skill's tool list name ``reject_evidence``.

Every tool is called through an in-memory MCP client session over stub connectors, so no test
reaches a network. Fixture values are synthetic.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from pydantic import ValidationError
from test_mcp_evidence import (
    BROADBAND,
    LAKE_GENEVA,
    NEW_BERN,
    SNOWFALL,
    _approve,
    _call,
    _fetch,
    _ok,
    _session,
    _stored,
    _text,
)

import lifescape.mcp_server as mcp_server
from lifescape.models import PlaceRecord
from lifescape.research import RejectionRequest, ReviewDecision, ReviewRecord
from lifescape.research_workspace import ResearchWorkspace

REPOSITORY = Path(__file__).resolve().parents[1]
REASON = "value does not match the source table"


def _reject(
    output_dir: Path,
    packet_id: str,
    *,
    reviewer: str = "Brett",
    reason: str = REASON,
    place_id: str = NEW_BERN,
    metric_id: str = BROADBAND,
) -> Any:
    return _call(
        output_dir,
        "reject_evidence",
        {
            "packet_id": packet_id,
            "place_id": place_id,
            "metric_id": metric_id,
            "reviewer": reviewer,
            "reason": reason,
        },
    )


# REQ-REJECT-1 ------------------------------------------------------------------------------


def test_req_reject_1_tool_has_a_strict_schema_and_is_annotated_destructive(
    tmp_path: Path,
) -> None:
    async def body(client: Client) -> dict[str, Any]:
        listed = await client.list_tools()
        return {tool.name: tool for tool in listed.tools}

    tools = _session(tmp_path, body)

    tool = tools["reject_evidence"]
    assert tool.input_schema["additionalProperties"] is False
    assert set(tool.input_schema["required"]) == {
        "packet_id",
        "place_id",
        "metric_id",
        "reviewer",
        "reason",
    }
    assert tool.annotations is not None
    assert tool.annotations.destructive_hint is True
    assert tool.annotations.read_only_hint is False
    assert tool.annotations.open_world_hint is False


def test_req_reject_1_records_a_named_reviewers_rejection(tmp_path: Path) -> None:
    packet_id = _fetch(tmp_path)["packet_id"]

    payload = _ok(_reject(tmp_path, packet_id, reviewer=" Brett ", reason=f"  {REASON}  "))

    assert payload["packet_id"] == packet_id
    assert payload["reviewer"] == "Brett"
    assert payload["reason"] == REASON
    assert payload["rejected"]["place_id"] == NEW_BERN
    assert payload["rejected"]["metric_id"] == BROADBAND
    assert payload["rejected"]["approved"] is False
    assert BROADBAND in payload["critical_metrics_needed"][NEW_BERN]
    stored = _stored(tmp_path, packet_id)
    assert stored.promotions == ()
    assert stored.reviews == (
        ReviewRecord(
            packet_id=packet_id,
            place_id=NEW_BERN,
            metric_id=BROADBAND,
            reviewer="Brett",
            decision=ReviewDecision.REJECTED,
            reason=REASON,
        ),
    )


def test_req_reject_1_records_through_research_reject_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    packet_id = _fetch(tmp_path)["packet_id"]
    calls: list[Any] = []
    original = mcp_server.record_rejection

    def spy(request: Any, **kwargs: Any) -> ReviewRecord:
        calls.append(request)
        return original(request, **kwargs)

    monkeypatch.setattr(mcp_server, "record_rejection", spy)

    _ok(_reject(tmp_path, packet_id))

    (request,) = calls
    assert (request.packet_id, request.place.place_id, request.metric_id) == (
        packet_id,
        NEW_BERN,
        BROADBAND,
    )
    assert request.reviewer == "Brett"


def test_req_reject_1_later_approval_of_the_value_is_refused(tmp_path: Path) -> None:
    packet_id = _fetch(tmp_path)["packet_id"]
    _ok(_reject(tmp_path, packet_id))

    approval = _approve(tmp_path, packet_id, "Brett")

    assert approval.is_error
    assert "already been rejected" in _text(approval)
    assert _stored(tmp_path, packet_id).promotions == ()


def test_req_reject_1_leaves_other_values_approvable(tmp_path: Path) -> None:
    packet_id = _fetch(tmp_path)["packet_id"]
    _ok(_reject(tmp_path, packet_id))

    other = _call(
        tmp_path,
        "approve_evidence",
        {
            "packet_id": packet_id,
            "place_id": LAKE_GENEVA,
            "metric_id": BROADBAND,
            "reviewer": "Brett",
        },
    )

    _ok(other)
    stored = _stored(tmp_path, packet_id)
    assert [item.observation.place.place_id for item in stored.promotions] == [LAKE_GENEVA]
    assert [item.decision for item in stored.reviews] == [
        ReviewDecision.REJECTED,
        ReviewDecision.APPROVED,
    ]


@pytest.mark.parametrize("reviewer", ["claude", "Claude ", "AI", "codex", "model", "", "  "])
def test_req_reject_1_refuses_an_ai_or_empty_reviewer_and_records_nothing(
    tmp_path: Path, reviewer: str
) -> None:
    packet_id = _fetch(tmp_path)["packet_id"]

    result = _reject(tmp_path, packet_id, reviewer=reviewer)

    assert result.is_error
    assert "nothing recorded" in _text(result)
    assert "reviewer" in _text(result)
    assert _stored(tmp_path, packet_id).reviews == ()


@pytest.mark.parametrize("reason", ["", "short", "        ", "  short   "])
def test_req_reject_1_refuses_a_missing_or_short_reason(tmp_path: Path, reason: str) -> None:
    packet_id = _fetch(tmp_path)["packet_id"]

    result = _reject(tmp_path, packet_id, reason=reason)

    assert result.is_error
    assert "nothing recorded" in _text(result)
    assert "reason" in _text(result)
    assert _stored(tmp_path, packet_id).reviews == ()


def test_req_reject_1_refuses_an_overlong_reason(tmp_path: Path) -> None:
    packet_id = _fetch(tmp_path)["packet_id"]

    result = _reject(tmp_path, packet_id, reason="x" * 1001)

    assert result.is_error
    assert "reason" in _text(result)
    assert _stored(tmp_path, packet_id).reviews == ()


def test_req_reject_1_refuses_a_second_rejection(tmp_path: Path) -> None:
    packet_id = _fetch(tmp_path)["packet_id"]
    _ok(_reject(tmp_path, packet_id))

    again = _reject(tmp_path, packet_id)

    assert again.is_error
    assert "already been rejected" in _text(again)
    assert len(_stored(tmp_path, packet_id).reviews) == 1


def test_req_reject_1_refuses_an_approved_value(tmp_path: Path) -> None:
    packet_id = _fetch(tmp_path)["packet_id"]
    _ok(_approve(tmp_path, packet_id, "Brett"))

    result = _reject(tmp_path, packet_id)

    assert result.is_error
    assert "already been approved" in _text(result)
    stored = _stored(tmp_path, packet_id)
    assert len(stored.promotions) == 1
    assert [item.decision for item in stored.reviews] == [ReviewDecision.APPROVED]


def test_req_reject_1_refuses_a_value_that_was_not_fetched(tmp_path: Path) -> None:
    packet_id = _fetch(tmp_path, [SNOWFALL])["packet_id"]

    missing = _reject(tmp_path, packet_id, metric_id=SNOWFALL)
    assert missing.is_error
    assert "not available" in _text(missing)

    stored = _stored(tmp_path, packet_id)
    ResearchWorkspace(tmp_path / "research-workspace").save(
        stored.model_copy(update={"fetch_history": ()})
    )
    unfetched = _reject(tmp_path, packet_id, metric_id=SNOWFALL)
    assert unfetched.is_error
    assert "not available" in _text(unfetched)
    assert _stored(tmp_path, packet_id).reviews == ()


def test_req_reject_1_refuses_an_unknown_or_malformed_packet(tmp_path: Path) -> None:
    unknown = _reject(tmp_path, "0123456789ab")
    assert unknown.is_error
    assert "not found" in _text(unknown)

    malformed = _reject(tmp_path, "../../etc/pw")
    assert malformed.is_error


# REQ-REJECT-2 ------------------------------------------------------------------------------


def test_req_reject_2_manifest_lists_reject_evidence() -> None:
    manifest = json.loads((REPOSITORY / "manifest.json").read_text("utf-8"))

    (entry,) = [tool for tool in manifest["tools"] if tool["name"] == "reject_evidence"]
    assert "reject" in entry["description"].lower()


def test_req_reject_2_skill_lists_reject_evidence() -> None:
    skill = (REPOSITORY / "skills/lifescape/SKILL.md").read_text("utf-8")
    section = skill.split("\n## Tools\n", 1)[1].split("\n## ", 1)[0]

    assert re.search(r"^- `reject_evidence`:", section, flags=re.MULTILINE)


def test_req_reject_1_research_refuses_a_reason_that_is_only_spaces() -> None:
    place = PlaceRecord(place_id=NEW_BERN, name="New Bern", state="NC")
    for reason in (" " * 12, "  short   "):
        with pytest.raises(ValidationError, match="reason must state why"):
            RejectionRequest(
                packet_id="0123456789ab",
                reviewer="Brett",
                place=place,
                metric_id=BROADBAND,
                reason=reason,
            )
