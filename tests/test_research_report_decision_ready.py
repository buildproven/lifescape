"""BUI-1995: research cards report a Decision ready bucket instead of raising."""

from datetime import UTC, date, datetime
from pathlib import Path

from lifescape.evidence_audit import AuditEntry, EvidenceAudit
from lifescape.models import (
    GateDefinition,
    GateOperator,
    GateResult,
    GateState,
    PlaceRecord,
    RunResult,
)
from lifescape.research_report import build_research_cards, write_research_report

GATES = (
    GateDefinition(
        id="winter", metric_id="annual_snowfall", operator=GateOperator.MAX, threshold=65
    ),
)


def _run(*place_ids: str) -> RunResult:
    return RunResult(
        run_id="run",
        profile_version="1",
        config_hash="hash",
        engine_version="1",
        evaluated_as_of=date(2026, 1, 1),
        evidence_through=datetime(2026, 1, 1, tzinfo=UTC),
        simulations=1000,
        sensitivity_seed=1,
        places=tuple(
            PlaceRecord(place_id=place_id, name=place_id.title(), state="NC")
            for place_id in place_ids
        ),
        observations=(),
        gate_results=tuple(
            GateResult(
                place_id=place_id,
                gate_id="winter",
                result=GateState.PASS,
                raw_value=20,
                threshold=65,
                source_url="https://example.gov",
                notes="passed",
            )
            for place_id in place_ids
        ),
        scores=(),
        sensitivity=(),
    )


def _audit(*place_ids: str) -> EvidenceAudit:
    return EvidenceAudit(
        entries=tuple(
            AuditEntry(
                place_id,
                place_id.title(),
                "NC",
                "annual_snowfall",
                20,
                "https://x",
                None,
                "ready",
                None,
            )
            for place_id in place_ids
        ),
        template_rows=(),
        as_of=date(2026, 1, 1),
    )


def test_bui_1995_decision_ready_town_gets_a_card_instead_of_raising() -> None:
    (card,) = build_research_cards(_run("ready"), _audit("ready"), GATES)

    assert card.bucket == "Decision ready"
    assert card.town == "Ready, NC"
    assert card.unresolved_critical_metrics == ()
    assert card.next_action is None
    assert card.source_url is None
    assert "validated evidence" in card.reason


def test_bui_1995_designated_lead_stays_investigate_now_when_decision_ready() -> None:
    (card,) = build_research_cards(
        _run("ready"), _audit("ready"), GATES, investigate_place_ids=("ready",)
    )

    assert card.bucket == "Investigate now"


def test_bui_1995_report_lists_and_counts_the_decision_ready_bucket(tmp_path: Path) -> None:
    cards = build_research_cards(_run("ready"), _audit("ready"), GATES)
    markdown, csv_path, _ = write_research_report(
        cards,
        tmp_path,
        strict_eligible_count=1,
        audit=_audit("ready"),
        has_synthetic_evidence=False,
    )

    text = markdown.read_text(encoding="utf-8")
    assert "- Decision ready: 1" in text
    assert "## Decision ready\n\n### Ready, NC" in text
    assert csv_path.read_text(encoding="utf-8").splitlines()[1].startswith("Decision ready,ready,")
