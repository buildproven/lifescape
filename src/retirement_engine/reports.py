"""Deterministic Markdown and CSV report rendering."""

from __future__ import annotations

import csv
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from retirement_engine.models import GateState, RunResult


def write_reports(run: RunResult, output_dir: Path) -> tuple[Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    place_names = {place.place_id: f"{place.name}, {place.state}" for place in run.places}
    sensitivity_by_place = {item.place_id: item for item in run.sensitivity}
    blocking_by_place: dict[str, list[object]] = {}
    for gate in run.gate_results:
        if gate.result in {GateState.FAIL, GateState.UNKNOWN}:
            blocking_by_place.setdefault(gate.place_id, []).append(gate)
    blocked = sorted(blocking_by_place)
    template_dir = Path(__file__).resolve().parent / "templates"
    environment = Environment(
        loader=FileSystemLoader(template_dir), undefined=StrictUndefined, autoescape=False
    )
    markdown = environment.get_template("comparison.md.j2").render(
        run=run,
        place_names=place_names,
        sensitivity_by_place=sensitivity_by_place,
        blocking_by_place=blocking_by_place,
        blocked=blocked,
    )
    markdown_path = output_dir / "comparison.md"
    markdown_path.write_text(markdown.rstrip() + "\n", encoding="utf-8")

    comparison_path = output_dir / "comparison.csv"
    with comparison_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(("rank", "place_id", "town", "score"))
        for score in run.scores:
            writer.writerow(
                (
                    score.rank,
                    score.place_id,
                    place_names[score.place_id],
                    f"{score.total_score:.6f}",
                )
            )

    sensitivity_path = output_dir / "sensitivity.csv"
    with sensitivity_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(
            ("place_id", "top_three_frequency", "mean_rank", "rank_variance", "fragile")
        )
        for item in run.sensitivity:
            writer.writerow(
                (
                    item.place_id,
                    f"{item.top_three_frequency:.6f}",
                    f"{item.mean_rank:.6f}",
                    f"{item.rank_variance:.6f}",
                    str(item.fragile).lower(),
                )
            )
    return markdown_path, comparison_path, sensitivity_path
