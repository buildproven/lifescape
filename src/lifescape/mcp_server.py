"""Local stdio MCP server over the discovery and evidence engines.

Agent-first surface (machine-setup ``docs/AGENT-FIRST.md``): the owner chats with Lifescape in
Claude Desktop. The tools call the same engines as the CLI and web app and keep their rules:
preferences discover, gates eliminate, weights rank, evidence decides. Discovery results stay
Tier C leads; only ``compare_finalists`` ranks, and only towns whose critical gates all pass.
"""

from __future__ import annotations

import csv
import hashlib
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date
from functools import cache, wraps
from pathlib import Path
from typing import Annotated, Any, cast

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.mcpserver.tools import Tool
from mcp.server.mcpserver.utilities.func_metadata import ArgModelBase
from pydantic import ConfigDict, Field

from lifescape.config import load_configuration, load_gates, load_metrics, load_sources
from lifescape.discovery import (
    DEFAULT_RECOMMENDATION_LIMIT,
    CatalogUnavailableError,
    DiscoveryService,
    SearchProfile,
)
from lifescape.discovery import load_catalog as load_place_catalog
from lifescape.evidence import validate_unique_headers
from lifescape.evidence_audit import audit_manual_evidence
from lifescape.models import GateState, RunResult
from lifescape.pipeline import execute_run
from lifescape.research_report import ResearchReportError, _next_action, build_research_cards
from lifescape.resources import bundled_benchmark

LIVE_RESEARCH_BRIEF = "research_brief.live.yaml"
MAX_FINALISTS = 25
MAX_DISCOVERY_LIMIT = 50

PlaceId = Annotated[str, Field(min_length=1, max_length=64)]
FilePath = Annotated[
    str,
    Field(
        min_length=1,
        max_length=4096,
        description="Path to a local wide evidence CSV (one row per town, one column per metric).",
    ),
]
IsoDate = Annotated[
    str | None,
    Field(
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="Evaluate freshness as of this ISO date (YYYY-MM-DD); default today.",
    ),
]


class EvidenceSelectionError(ValueError):
    """Raised when the requested towns cannot be selected from an evidence CSV."""


ToolFunction = Callable[..., dict[str, Any]]


def _reporting(fn: ToolFunction) -> ToolFunction:
    """Show the agent why a request failed.

    The engines raise ``ValueError`` subclasses for bad profiles, evidence, and configuration,
    and ``OSError`` for unreadable files; the MCP SDK hides any non-``ToolError`` message from
    the client, so these become ``ToolError``. Other exceptions stay crashes.
    """

    @wraps(fn)
    def run(*args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return fn(*args, **kwargs)
        except (ValueError, OSError, CatalogUnavailableError) as exc:
            raise ToolError(str(exc)) from exc

    return run


def _register(fn: ToolFunction) -> Tool:
    """Build a tool that rejects unknown arguments (``additionalProperties: false``)."""
    tool = Tool.from_function(fn)
    tool.fn = _reporting(fn)
    base = tool.fn_metadata.arg_model
    strict_model = cast(
        type[ArgModelBase],
        type(
            base.__name__,
            (base,),
            {"model_config": ConfigDict(extra="forbid"), "__module__": __name__},
        ),
    )
    tool.fn_metadata.arg_model = strict_model
    tool.parameters = strict_model.model_json_schema(by_alias=True)
    return tool


def _existing_file(raw: str, label: str) -> Path:
    path = Path(raw).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"{label} not found: {path}")
    return path


def _parse_date(value: str | None) -> date | None:
    return None if value is None else date.fromisoformat(value)


def _select_evidence(evidence: Path, place_ids: tuple[str, ...], destination: Path) -> Path:
    """Copy only the requested towns' rows, verbatim, so ranks compare finalists only."""
    with evidence.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or "place_id" not in reader.fieldnames:
            raise EvidenceSelectionError(f"evidence CSV has no place_id column: {evidence}")
        validate_unique_headers(reader.fieldnames)
        fieldnames = list(reader.fieldnames)
        wanted = set(place_ids)
        rows = [row for row in reader if row["place_id"] in wanted]
    missing = sorted(wanted - {row["place_id"] for row in rows})
    if missing:
        raise EvidenceSelectionError(f"evidence CSV has no rows for place IDs: {missing}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return destination


def _work_dir(root: Path, kind: str, evidence: Path, place_ids: tuple[str, ...]) -> Path:
    digest = hashlib.sha256()
    digest.update(str(evidence.resolve()).encode())
    digest.update(evidence.read_bytes())
    digest.update("\0".join(sorted(place_ids)).encode())
    return root / f"{kind}-{digest.hexdigest()[:12]}"


def _gate_payload(run: RunResult, metric_by_gate: dict[str, str]) -> dict[str, list[Any]]:
    by_place: dict[str, list[Any]] = {place.place_id: [] for place in run.places}
    for gate in run.gate_results:
        by_place[gate.place_id].append(
            {
                "gate_id": gate.gate_id,
                "metric_id": metric_by_gate.get(gate.gate_id),
                "result": gate.result.value,
                "raw_value": gate.raw_value,
                "threshold": gate.threshold,
                "source_url": gate.source_url,
                "notes": gate.notes,
            }
        )
    return by_place


def create_server(*, output_dir: Path, config_dir: Path | None = None) -> MCPServer:
    """Build the Lifescape MCP server. ``config_dir`` defaults to the packaged config."""

    @contextmanager
    def configuration() -> Iterator[Path]:
        if config_dir is not None:
            yield config_dir
            return
        with bundled_benchmark() as (_, packaged_config):
            yield packaged_config

    @cache
    def discovery() -> DiscoveryService:
        return DiscoveryService(load_place_catalog())

    def run_engine(
        config: Path,
        evidence: Path,
        place_ids: tuple[str, ...],
        work_dir: Path,
        as_of: date | None,
    ) -> RunResult:
        brief = config / LIVE_RESEARCH_BRIEF
        if load_configuration(config, None, brief).research_brief.benchmark_only:
            raise ValueError(f"{brief} is benchmark-only; real comparisons need a live brief")
        selected = _select_evidence(evidence, place_ids, work_dir / "selected-evidence.csv")
        return execute_run(
            evidence_path=selected,
            config_dir=config,
            research_brief_path=brief,
            database_path=work_dir / "run.sqlite",
            output_dir=work_dir / "reports",
            as_of=as_of,
        )

    def find_place(
        query: Annotated[
            str,
            Field(min_length=1, max_length=120, description="Town name or 'Name, ST'."),
        ],
        limit: Annotated[int, Field(ge=1, le=25)] = 10,
    ) -> dict[str, Any]:
        """Look up U.S. places in the packaged catalog by name; returns place IDs to use as
        example towns in discover_towns. Exact matches come first, then prefix, then substring."""
        matches = discovery().catalog.lookup(query, limit=limit)
        return {
            "query": query,
            "matches": [
                {
                    "place_id": place.place_id,
                    "name": place.name,
                    "state": place.state,
                    "region": place.region,
                    "label": place.label,
                    "population": place.population,
                    "can_be_example": place.serving_eligible,
                }
                for place in matches
            ],
        }

    def discover_towns(
        profile: SearchProfile,
        limit: Annotated[int, Field(ge=1, le=MAX_DISCOVERY_LIMIT)] = DEFAULT_RECOMMENDATION_LIMIT,
    ) -> dict[str, Any]:
        """Find towns that feel like the profile: up to two example towns (place IDs from
        find_place), explicit targets, priorities 1-5, hard min/max limits, and state or region
        filters. Results are discovery leads from catalog data, not verified evidence."""
        result = discovery().search(profile, limit=limit)
        return {
            "profile": result["profile"],
            "catalog_version": result["catalog_version"],
            "algorithm_version": result["algorithm_version"],
            "evidence_status": "discovery leads; not verified evidence",
            "matches": [
                {
                    "rank": match["rank"],
                    "place_id": match["place_id"],
                    "label": match["label"],
                    "population": match["population"],
                    "match_percent": match["match_percent"],
                    "match_band": match["match_band"],
                    "feel_summary": match["feel_summary"],
                    "reasons": match["reasons"],
                    "trade_offs": match["differences"],
                    "unknowns": {
                        "missing_fields": match["missing_fields"],
                        "unknown_constraints": match["unknown_constraints"],
                    },
                }
                for match in result["recommendations"]
            ],
            "diagnostics": result["diagnostics"],
        }

    def compare_finalists(
        place_ids: Annotated[
            list[PlaceId],
            Field(min_length=2, max_length=MAX_FINALISTS, description="Finalist place IDs."),
        ],
        evidence_path: FilePath,
        as_of: IsoDate = None,
    ) -> dict[str, Any]:
        """Compare finalists on reviewed evidence with the strict engine: critical gates
        eliminate (PASS, FAIL, UNKNOWN, WAIVED), weights rank the survivors, and a sensitivity
        check shows how stable the ranks are. A finalist with failed or unknown critical
        evidence is blocked and never ranked. Writes Markdown and CSV reports."""
        ids = tuple(place_ids)
        if len(set(ids)) != len(ids):
            raise ValueError("place_ids must be distinct")
        evidence = _existing_file(evidence_path, "evidence CSV")
        work_dir = _work_dir(output_dir, "compare", evidence, ids)
        with configuration() as config:
            run = run_engine(config, evidence, ids, work_dir, _parse_date(as_of))
            metric_ids = [metric.id for metric in load_metrics(config)]
            metric_by_gate = {gate.id: gate.metric_id for gate in load_gates(config).gates}
        gates_by_place = _gate_payload(run, metric_by_gate)
        scores = {score.place_id: score for score in run.scores}
        sensitivity = {item.place_id: item for item in run.sensitivity}
        observed = {(item.place.place_id, item.metric_id) for item in run.observations}
        finalists = []
        for place in run.places:
            score = scores.get(place.place_id)
            gates = gates_by_place[place.place_id]
            stability = sensitivity.get(place.place_id)
            finalists.append(
                {
                    "place_id": place.place_id,
                    "town": f"{place.name}, {place.state}",
                    "status": "ranked" if score is not None else "blocked",
                    "rank": None if score is None else score.rank,
                    "total_score": None if score is None else score.total_score,
                    "blocked_by": [
                        {"gate_id": gate["gate_id"], "result": gate["result"]}
                        for gate in gates
                        if gate["result"] in {GateState.FAIL.value, GateState.UNKNOWN.value}
                    ],
                    "gates": gates,
                    "unknowns": [
                        metric_id
                        for metric_id in metric_ids
                        if (place.place_id, metric_id) not in observed
                    ],
                    "sensitivity": None if stability is None else stability.model_dump(),
                }
            )
        report_dir = work_dir / "reports"
        has_synthetic_evidence = any(item.source.synthetic for item in run.observations)
        return {
            "run_id": run.run_id,
            "evaluated_as_of": run.evaluated_as_of.isoformat(),
            "evidence_through": run.evidence_through.isoformat(),
            "synthetic_evidence": has_synthetic_evidence,
            "finalists": finalists,
            "ranking": [
                {"rank": score.rank, "place_id": score.place_id, "total_score": score.total_score}
                for score in run.scores
            ],
            "blocked": [item["place_id"] for item in finalists if item["status"] == "blocked"],
            "reports": {
                "markdown": str(report_dir / "comparison.md"),
                "ranking_csv": str(report_dir / "comparison.csv"),
                "sensitivity_csv": str(report_dir / "sensitivity.csv"),
                "database": str(work_dir / "run.sqlite"),
            },
        }

    def explain_town(
        place_id: PlaceId,
        evidence_path: FilePath,
        manifest_path: Annotated[
            str | None,
            Field(
                min_length=1,
                max_length=4096,
                description="Optional per-metric provenance manifest CSV for the evidence.",
            ),
        ] = None,
        as_of: IsoDate = None,
    ) -> dict[str, Any]:
        """Explain where one town stands on the evidence: its research card (known reject,
        insufficient evidence, or decision ready), the critical evidence still unresolved, and
        the next research action for each unresolved metric. This does not rank towns."""
        evidence = _existing_file(evidence_path, "evidence CSV")
        manifest = None if manifest_path is None else _existing_file(manifest_path, "manifest")
        evaluated_as_of = _parse_date(as_of)
        work_dir = _work_dir(output_dir, "explain", evidence, (place_id,))
        with configuration() as config:
            run = run_engine(config, evidence, (place_id,), work_dir, evaluated_as_of)
            audit = audit_manual_evidence(
                evidence,
                load_metrics(config),
                load_sources(config),
                manifest_path=manifest,
                as_of=evaluated_as_of,
            )
            gates = load_gates(config).gates
        place = run.places[0]
        synthetic = any(item.source.synthetic for item in run.observations)
        try:
            (card,) = build_research_cards(run, audit, gates)
        except ResearchReportError:
            # Raised only when every critical gate has ready, validated evidence.
            return {
                "place_id": place.place_id,
                "town": f"{place.name}, {place.state}",
                "bucket": "Decision ready",
                "reason": "Every critical gate has validated evidence; compare it with others",
                "unresolved_critical_metrics": [],
                "next_action": None,
                "next_actions": [],
                "source_url": None,
                "synthetic_evidence": synthetic,
            }
        return {
            "place_id": card.place_id,
            "town": card.town,
            "bucket": card.bucket,
            "reason": card.reason,
            "unresolved_critical_metrics": list(card.unresolved_critical_metrics),
            "next_action": card.next_action,
            "next_actions": [
                {"metric_id": metric_id, "action": _next_action(metric_id)}
                for metric_id in card.unresolved_critical_metrics
            ],
            "source_url": card.source_url,
            "synthetic_evidence": synthetic,
        }

    tools: list[ToolFunction] = [
        find_place,
        discover_towns,
        compare_finalists,
        explain_town,
    ]
    return MCPServer(
        "lifescape",
        instructions=(
            "Lifescape helps choose a retirement town. Use find_place to get place IDs, "
            "discover_towns to find towns that feel like the owner's examples (leads only), "
            "compare_finalists to rank finalists on reviewed evidence, and explain_town for "
            "the research still needed. Never present a blocked town as ranked."
        ),
        tools=[_register(tool) for tool in tools],
    )


def serve(*, output_dir: Path, config_dir: Path | None = None) -> None:
    """Serve MCP over stdio until the client disconnects."""
    create_server(output_dir=output_dir, config_dir=config_dir).run("stdio")
