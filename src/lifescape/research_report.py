"""Conditional research shortlists that do not rank towns."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path

from lifescape.evidence_audit import EvidenceAudit, audit_rows_by_place
from lifescape.models import GateDefinition, GateResult, GateState, RunResult


class ResearchReportError(ValueError):
    """Raised when a conditional research shortlist is requested incorrectly."""


@dataclass(frozen=True)
class ResearchCard:
    place_id: str
    town: str
    bucket: str
    reason: str
    unresolved_critical_metrics: tuple[str, ...]
    next_action: str | None
    source_url: str | None


def build_research_cards(
    run: RunResult,
    audit: EvidenceAudit,
    gates: tuple[GateDefinition, ...],
    *,
    investigate_place_ids: tuple[str, ...] = (),
) -> tuple[ResearchCard, ...]:
    """Classify every town once without changing strict gate eligibility."""
    places = {place.place_id: place for place in run.places}
    unknown_designations = sorted(set(investigate_place_ids) - set(places))
    if unknown_designations:
        raise ResearchReportError(f"unknown investigation leads: {unknown_designations}")
    audit_by_place = audit_rows_by_place(audit.entries)
    gate_by_id = {gate.id: gate for gate in gates}
    gates_by_place: dict[str, list[GateResult]] = {place_id: [] for place_id in places}
    for gate_result in run.gate_results:
        gates_by_place[gate_result.place_id].append(gate_result)

    cards: list[ResearchCard] = []
    for place_id in sorted(places):
        place = places[place_id]
        audit_by_metric = {entry.metric_id: entry for entry in audit_by_place.get(place_id, ())}
        results_by_gate = {result.gate_id: result for result in gates_by_place[place_id]}
        failed = next(
            (
                result
                for gate in gates
                if (result := results_by_gate.get(gate.id)) is not None
                if result.result is GateState.FAIL
                and audit_by_metric.get(gate.metric_id) is not None
                and audit_by_metric[gate.metric_id].status == "ready"
            ),
            None,
        )
        unresolved = tuple(
            gate.metric_id
            for gate in gates
            if audit_by_metric.get(gate.metric_id) is None
            or audit_by_metric[gate.metric_id].status != "ready"
            or results_by_gate.get(gate.id) is None
            or results_by_gate[gate.id].result is GateState.UNKNOWN
        )
        if failed is not None:
            metric_id = gate_by_id[failed.gate_id].metric_id
            cards.append(
                ResearchCard(
                    place_id=place_id,
                    town=f"{place.name}, {place.state}",
                    bucket="Known reject",
                    reason=(
                        f"{metric_id} failed its threshold "
                        f"({failed.raw_value:g} vs {failed.threshold:g})"
                    ),
                    unresolved_critical_metrics=unresolved,
                    next_action=None,
                    source_url=(audit_by_metric[metric_id].validated_provenance or {}).get(
                        "source_url", audit_by_metric[metric_id].supplied_source_url
                    ),
                )
            )
            continue
        next_metric = unresolved[0] if unresolved else None
        if place_id in investigate_place_ids:
            cards.append(
                ResearchCard(
                    place_id=place_id,
                    town=f"{place.name}, {place.state}",
                    bucket="Investigate now",
                    reason="User-designated discovery lead; not decision-ready",
                    unresolved_critical_metrics=unresolved,
                    next_action=_next_action(next_metric),
                    source_url=None,
                )
            )
            continue
        if not unresolved:
            raise ResearchReportError(
                f"{place_id!r} is decision-ready; either omit it from this research queue "
                "or designate it as an investigation lead"
            )
        cards.append(
            ResearchCard(
                place_id=place_id,
                town=f"{place.name}, {place.state}",
                bucket="Insufficient evidence",
                reason="No validated rejection, but critical evidence is unresolved",
                unresolved_critical_metrics=unresolved,
                next_action=_next_action(next_metric),
                source_url=None,
            )
        )
    return tuple(cards)


def write_research_report(
    cards: tuple[ResearchCard, ...],
    output_dir: Path,
    *,
    strict_eligible_count: int,
    audit: EvidenceAudit,
    has_synthetic_evidence: bool,
) -> tuple[Path, Path, Path]:
    """Write deterministic Markdown and CSV research artifacts without score fields."""
    output_dir.mkdir(parents=True, exist_ok=True)
    buckets = ("Investigate now", "Known reject", "Insufficient evidence")
    counts = {bucket: sum(card.bucket == bucket for card in cards) for bucket in buckets}
    markdown_lines = [
        "# Retirement research shortlist",
        "",
        "This is a research queue, not a town ranking or purchase recommendation.",
        "",
        *(
            [
                "> **Synthetic evidence warning:** This shortlist contains synthetic test data. "
                "It must not support a real purchase decision.",
                "",
            ]
            if has_synthetic_evidence
            else []
        ),
        "## Field summary",
        "",
        *(f"- {bucket}: {counts[bucket]}" for bucket in buckets),
        f"- Strict engine eligible: {strict_eligible_count}",
    ]
    for bucket in buckets:
        markdown_lines.extend(("", f"## {bucket}", ""))
        bucket_cards = [card for card in cards if card.bucket == bucket]
        if not bucket_cards:
            markdown_lines.append("None.")
        for card in bucket_cards:
            markdown_lines.append(f"### {card.town}")
            markdown_lines.append(f"- Reason: {card.reason}")
            if card.source_url is not None:
                markdown_lines.append(f"- Source: {card.source_url}")
            if card.unresolved_critical_metrics:
                markdown_lines.append(
                    "- Unresolved critical evidence: " + ", ".join(card.unresolved_critical_metrics)
                )
            if card.next_action is not None:
                markdown_lines.append(f"- Next action: {card.next_action}")
            markdown_lines.append("")
    markdown_path = output_dir / "research-shortlist.md"
    markdown_path.write_text("\n".join(markdown_lines).rstrip() + "\n", encoding="utf-8")

    audit_path = output_dir / "provenance-audit.json"
    audit_path.write_text(
        json.dumps(audit.as_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    csv_path = output_dir / "research-shortlist.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "bucket",
                "place_id",
                "town",
                "reason",
                "unresolved_critical_metrics",
                "next_action",
                "source_url",
            ),
            lineterminator="\n",
        )
        writer.writeheader()
        for card in cards:
            writer.writerow(
                {
                    "bucket": card.bucket,
                    "place_id": card.place_id,
                    "town": card.town,
                    "reason": card.reason,
                    "unresolved_critical_metrics": ";".join(card.unresolved_critical_metrics),
                    "next_action": card.next_action or "",
                    "source_url": card.source_url or "",
                }
            )
    return markdown_path, csv_path, audit_path


def _next_action(metric_id: str | None) -> str | None:
    if metric_id is None:
        return None
    actions = {
        "median_sale_price": "Record a metric-correct median sale-price source and date.",
        "er_drive_minutes": "Verify route time to an emergency-services hospital.",
        "broadband_mbps_down": "Record an address or town broadband availability source.",
        "annual_snowfall": "Record a station/year snowfall observation and source.",
        "flood_risk_score": (
            "Review flood exposure for finalist locations before property selection."
        ),
        "distress_index": "Record the configured Census-derived distress observation.",
        "one_level_inventory_count": "Run and record the repeatable one-level listing search.",
    }
    return actions.get(metric_id, f"Record a metric-correct {metric_id} source and date.")
