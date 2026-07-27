from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from lifescape.evidence_audit import AuditEntry, EvidenceAudit
from lifescape.models import (
    GateDefinition,
    GateOperator,
    GateResult,
    GateState,
    PlaceRecord,
    RunResult,
)
from lifescape.research_report import (
    ResearchReportError,
    build_research_cards,
    write_research_report,
)


def _run() -> RunResult:
    places = (
        PlaceRecord(place_id="lead", name="Lead", state="NC"),
        PlaceRecord(place_id="reject", name="Reject", state="NC"),
        PlaceRecord(place_id="unknown", name="Unknown", state="NC"),
    )
    return RunResult(
        run_id="run",
        profile_version="1",
        config_hash="hash",
        engine_version="1",
        evaluated_as_of=date(2026, 1, 1),
        evidence_through=datetime(2026, 1, 1, tzinfo=UTC),
        simulations=1000,
        sensitivity_seed=1,
        places=places,
        observations=(),
        gate_results=(
            GateResult(
                place_id="lead", gate_id="winter", result=GateState.UNKNOWN, raw_value=None,
                threshold=65, source_url=None, notes="missing"
            ),
            GateResult(
                place_id="reject", gate_id="winter", result=GateState.FAIL, raw_value=90,
                threshold=65, source_url="https://example.gov", notes="failed"
            ),
            GateResult(
                place_id="unknown", gate_id="winter", result=GateState.UNKNOWN, raw_value=None,
                threshold=65, source_url=None, notes="missing"
            ),
        ),
        scores=(),
        sensitivity=(),
    )


def _audit() -> EvidenceAudit:
    return EvidenceAudit(
        entries=(
            AuditEntry(
                "lead", "Lead", "NC", "annual_snowfall", 20, "https://x", "ready", None
            ),
            AuditEntry(
                "reject", "Reject", "NC", "annual_snowfall", 90, "https://x", "ready", None
            ),
            AuditEntry(
                "unknown",
                "Unknown",
                "NC",
                "annual_snowfall",
                20,
                "https://x",
                "action_required",
                "missing provenance",
            ),
        ),
        template_rows=(),
    )


def test_research_cards_use_audit_before_known_reject() -> None:
    cards = build_research_cards(
        _run(),
        _audit(),
        (
            GateDefinition(
                id="winter", metric_id="annual_snowfall", operator=GateOperator.MAX, threshold=65
            ),
        ),
        investigate_place_ids=("lead",),
    )

    assert [card.bucket for card in cards] == [
        "Investigate now",
        "Known reject",
        "Insufficient evidence",
    ]
    assert cards[0].reason.endswith("not decision-ready")
    assert cards[1].reason == "annual_snowfall failed its threshold (90 vs 65)"
    assert cards[2].next_action == "Record a station/year snowfall observation and source."


def test_report_lists_each_town_once_and_omits_ranking_fields(tmp_path: Path) -> None:
    cards = build_research_cards(
        _run(),
        _audit(),
        (
            GateDefinition(
                id="winter", metric_id="annual_snowfall", operator=GateOperator.MAX, threshold=65
            ),
        ),
        investigate_place_ids=("lead",),
    )
    markdown, csv_path = write_research_report(cards, tmp_path, strict_eligible_count=0)

    assert markdown.read_text(encoding="utf-8").count("### ") == 3
    assert "Rank" not in markdown.read_text(encoding="utf-8")
    assert "Score" not in markdown.read_text(encoding="utf-8")
    assert "rank" not in csv_path.read_text(encoding="utf-8").splitlines()[0]
    assert "score" not in csv_path.read_text(encoding="utf-8").splitlines()[0]


def test_research_cards_reject_unknown_designation() -> None:
    with pytest.raises(ResearchReportError, match="unknown investigation leads"):
        build_research_cards(
            _run(),
            _audit(),
            (
                GateDefinition(
                    id="winter",
                    metric_id="annual_snowfall",
                    operator=GateOperator.MAX,
                    threshold=65,
                ),
            ),
            investigate_place_ids=("missing",),
        )
