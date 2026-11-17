"""Local stdio MCP server over the discovery and evidence engines.

Agent-first surface (machine-setup ``docs/AGENT-FIRST.md``): the owner chats with Lifescape in
Claude Desktop. The tools call the same engines as the CLI and web app and keep their rules:
preferences discover, gates eliminate, weights rank, evidence decides. Discovery results stay
Tier C leads; only ``compare_finalists`` ranks, and only towns whose critical gates all pass.
Live-fetched evidence stays a proposal until ``approve_evidence`` records a named human reviewer.
"""

from __future__ import annotations

import csv
import hashlib
import os
import sqlite3
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import date
from functools import cache, wraps
from pathlib import Path
from typing import Annotated, Any, cast
from uuid import uuid4

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.mcpserver.tools import Tool
from mcp.server.mcpserver.utilities.func_metadata import ArgModelBase
from mcp_types import ToolAnnotations
from pydantic import ConfigDict, Field, ValidationError

from lifescape.config import load_configuration, load_gates, load_metrics, load_sources
from lifescape.connectors import CensusAcsConnector, Connector, NoaaGsoyConnector
from lifescape.discovery import (
    DEFAULT_RECOMMENDATION_LIMIT,
    CatalogUnavailableError,
    DiscoveryService,
    SearchProfile,
)
from lifescape.discovery import load_catalog as load_place_catalog
from lifescape.evidence import EvidenceError, ingest_csv, validate_unique_headers
from lifescape.evidence_audit import EvidenceAudit, audit_manual_evidence
from lifescape.models import GateState, ObservationRecord, PlaceRecord, RunResult, StrictModel
from lifescape.pipeline import execute_run
from lifescape.research import (
    MAX_DISCOVERY_LEADS,
    DiscoveryLead,
    FetchedPromotionRequest,
    ReviewDecision,
    ReviewRecord,
    SearchBrief,
    create_packet,
    promote_fetched_evidence,
    readiness_for,
    state_for,
)
from lifescape.research_report import ResearchReportError, _next_action, build_research_cards
from lifescape.research_sources import ConnectorEvidenceProvider, EvidenceFetchResult
from lifescape.research_workspace import ResearchWorkspace, StoredResearchPacket
from lifescape.resources import bundled_benchmark

LIVE_RESEARCH_BRIEF = "research_brief.live.yaml"
MAX_FINALISTS = 25
MAX_DISCOVERY_LIMIT = 50
RESEARCH_WORKSPACE = "research-workspace"
RUN_INDEX = "runs"
FETCH_PROVIDER = "mcp:fetch_live_evidence"
FETCH_BRIEF = SearchBrief(
    preferences="Live public-source evidence the owner requested through the Lifescape MCP server."
)
FETCH_RATIONALE = "The owner named this town for live evidence through the MCP server."

PlaceId = Annotated[str, Field(min_length=1, max_length=64)]
MetricId = Annotated[str, Field(min_length=1, max_length=64)]
PacketId = Annotated[
    str,
    Field(pattern=r"^[a-f0-9]{12}$", description="Research packet ID from fetch_live_evidence."),
]
RunId = Annotated[
    str, Field(pattern=r"^[a-f0-9]{16}$", description="Run ID from compare_finalists.")
]
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


class RunReportIndex(StrictModel):
    """The reports one ``compare_finalists`` run wrote, with digests to detect overwrites."""

    run_id: str
    reports: dict[str, str]
    sha256: dict[str, str]


ToolFunction = Callable[..., dict[str, Any]]
ConnectorFactory = Callable[[], Sequence[Connector]]


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


def _register(fn: ToolFunction, annotations: ToolAnnotations | None = None) -> Tool:
    """Build a tool that rejects unknown arguments (``additionalProperties: false``)."""
    tool = Tool.from_function(fn, annotations=annotations)
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


def _default_connectors() -> Sequence[Connector]:
    """Fresh connectors per fetch, so each carries the day it retrieved its data."""
    return (CensusAcsConnector(), NoaaGsoyConnector())


def _census_geographies(place_id: str) -> dict[str, str]:
    """A catalog place ID is its 7-digit Census place GEOID: 2-digit state + 5-digit place.

    NOAA geography is never derived here: a station is a source choice for a human, so a fetch
    without one reports the geography as missing.
    """
    if len(place_id) == 7 and place_id.isdigit():
        return {CensusAcsConnector.name: f"{place_id[:2]}:{place_id[2:]}"}
    return {}


def _observation_payload(observation: ObservationRecord, *, approved: bool) -> dict[str, Any]:
    return {
        "place_id": observation.place.place_id,
        "town": f"{observation.place.name}, {observation.place.state}",
        "metric_id": observation.metric_id,
        "raw_value": observation.raw_value,
        "observed_period": observation.observed_period,
        "observed_at": observation.observed_at.isoformat(),
        "source": observation.source.model_dump(mode="json"),
        "approved": approved,
    }


def _audit_summary(audit: EvidenceAudit, metric_ids: Sequence[str]) -> list[dict[str, Any]]:
    by_place: dict[str, list[Any]] = {}
    towns: dict[str, str] = {}
    for entry in audit.entries:
        towns.setdefault(entry.place_id, f"{entry.place_name}, {entry.state}")
        by_place.setdefault(entry.place_id, []).append(entry)
    summary = []
    for place_id, entries in by_place.items():
        present = {entry.metric_id for entry in entries}
        summary.append(
            {
                "place_id": place_id,
                "town": towns[place_id],
                "ready_count": sum(entry.status == "ready" for entry in entries),
                "finding_count": sum(entry.status != "ready" for entry in entries),
                "missing_metrics": [item for item in metric_ids if item not in present],
                "metrics": [
                    {
                        "metric_id": entry.metric_id,
                        "raw_value": entry.raw_value,
                        "status": entry.status,
                        "reason": entry.reason,
                        "validated_provenance": entry.validated_provenance,
                    }
                    for entry in entries
                ],
            }
        )
    return summary


def _human_reviewer(
    packet_id: str, place_id: str, metric_id: str, reviewer: str
) -> FetchedPromotionRequest:
    """Apply the research boundary's reviewer check; its message names the rule broken."""
    try:
        return FetchedPromotionRequest(
            packet_id=packet_id, place_id=place_id, metric_id=metric_id, reviewer=reviewer
        )
    except ValidationError as exc:
        reasons = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: "
            f"{str(error['msg']).removeprefix('Value error, ')}"
            for error in exc.errors()
        )
        raise ValueError(f"approval refused, nothing promoted: {reasons}") from exc


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _database_has_run(database: Path, run_id: str) -> bool:
    try:
        with sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True) as connection:
            row = connection.execute(
                "SELECT 1 FROM research_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
    except sqlite3.Error as exc:
        raise ValueError(f"cannot read the run database {database}: {exc}") from exc
    return row is not None


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


def create_server(
    *,
    output_dir: Path,
    config_dir: Path | None = None,
    live_connectors: ConnectorFactory = _default_connectors,
) -> MCPServer:
    """Build the Lifescape MCP server. ``config_dir`` defaults to the packaged config;
    ``live_connectors`` builds the public-data connectors each ``fetch_live_evidence`` uses."""
    workspace = ResearchWorkspace(output_dir / RESEARCH_WORKSPACE)

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
        reports = {
            "markdown": str(report_dir / "comparison.md"),
            "ranking_csv": str(report_dir / "comparison.csv"),
            "sensitivity_csv": str(report_dir / "sensitivity.csv"),
            "database": str(work_dir / "run.sqlite"),
        }
        index = RunReportIndex(
            run_id=run.run_id,
            reports=reports,
            sha256={key: _sha256(Path(path)) for key, path in reports.items() if key != "database"},
        )
        _write_atomic(
            output_dir / RUN_INDEX / f"{run.run_id}.json", index.model_dump_json(indent=2) + "\n"
        )
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
            "reports": reports,
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

    def inspect_evidence(
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
        """Check an evidence CSV before using it: the strict loader's validation errors, and
        for each town and metric the audit status (ready, or action required and why) and the
        metrics still missing. This reads the file only; it does not rank or change anything."""
        evidence = _existing_file(evidence_path, "evidence CSV")
        manifest = None if manifest_path is None else _existing_file(manifest_path, "manifest")
        evaluated_as_of = _parse_date(as_of)
        errors: list[str] = []
        audit: EvidenceAudit | None = None
        with configuration() as config:
            loaded = load_configuration(config, None, config / LIVE_RESEARCH_BRIEF)
            try:
                ingest_csv(
                    evidence,
                    loaded.metrics,
                    loaded.sources,
                    as_of=evaluated_as_of,
                    required_scope=loaded.research_brief.scope,
                )
            except EvidenceError as exc:
                errors.append(str(exc))
            try:
                audit = audit_manual_evidence(
                    evidence,
                    loaded.metrics,
                    loaded.sources,
                    manifest_path=manifest,
                    as_of=evaluated_as_of,
                )
            except EvidenceError as exc:
                errors.append(str(exc))
        metric_ids = [metric.id for metric in loaded.metrics]
        return {
            "evidence_path": str(evidence),
            "valid": not errors,
            "errors": errors,
            "as_of": None if audit is None else audit.as_of.isoformat(),
            "observation_count": 0 if audit is None else len(audit.entries),
            "finding_count": 0 if audit is None else audit.finding_count,
            "towns": [] if audit is None else _audit_summary(audit, metric_ids),
        }

    def fetch_live_evidence(
        place_ids: Annotated[
            list[PlaceId],
            Field(
                min_length=1,
                max_length=MAX_DISCOVERY_LEADS,
                description="Catalog place IDs (from find_place) to fetch evidence for.",
            ),
        ],
        metric_ids: Annotated[
            list[MetricId],
            Field(min_length=1, max_length=64, description="Configured metric IDs to fetch."),
        ],
    ) -> dict[str, Any]:
        """Fetch live public-source evidence (Census ACS, NOAA) for towns into a new research
        packet. Every returned observation is a proposal, not evidence: show each value and its
        source to the owner, and call approve_evidence only after the owner confirms it. Fetch
        failures and missing source geographies are listed, never filled in."""
        ids, wanted = tuple(place_ids), tuple(metric_ids)
        if len(set(ids)) != len(ids):
            raise ValueError("place_ids must be distinct")
        if len(set(wanted)) != len(wanted):
            raise ValueError("metric_ids must be distinct")
        with configuration() as config:
            known = {metric.id for metric in load_metrics(config)}
        unknown = sorted(set(wanted) - known)
        if unknown:
            raise ValueError(f"unknown metric IDs: {unknown}")
        connectors = tuple(
            connector
            for connector in live_connectors()
            if set(connector.supported_metric_ids) & set(wanted)
        )
        fetchable = {
            metric for connector in connectors for metric in connector.supported_metric_ids
        }
        unsupported = sorted(set(wanted) - fetchable)
        if unsupported:
            raise ValueError(f"no live connector fetches metrics: {unsupported}")
        catalog = discovery().catalog
        missing_places = [place_id for place_id in ids if place_id not in catalog.places]
        if missing_places:
            raise ValueError(f"place IDs are not in the catalog: {missing_places}")
        leads = tuple(
            DiscoveryLead(
                place=PlaceRecord(
                    place_id=place_id,
                    name=catalog.places[place_id].name,
                    state=catalog.places[place_id].state,
                ),
                rationale=FETCH_RATIONALE,
                connector_geographies=_census_geographies(place_id),
            )
            for place_id in ids
        )
        packet = create_packet(FETCH_BRIEF, leads, discovery_provider=FETCH_PROVIDER)
        fetched = ConnectorEvidenceProvider(connectors=connectors).fetch(packet)
        proposed = tuple(item for item in fetched.observations if item.metric_id in wanted)
        result = EvidenceFetchResult(observations=proposed, errors=fetched.errors)
        workspace.save(
            StoredResearchPacket(packet=packet, fetch_history=(workspace.snapshot(result),))
        )
        found = {(item.place.place_id, item.metric_id) for item in proposed}
        return {
            "packet_id": packet.id,
            "state": packet.state.value,
            "evidence_status": "proposed; not evidence until the owner approves each value",
            "observations": [_observation_payload(item, approved=False) for item in proposed],
            "missing": [
                {"place_id": place_id, "metric_id": metric_id}
                for place_id in ids
                for metric_id in wanted
                if (place_id, metric_id) not in found
            ],
            "errors": {place_id: list(values) for place_id, values in result.errors.items()},
        }

    def approve_evidence(
        packet_id: PacketId,
        place_id: PlaceId,
        metric_id: MetricId,
        reviewer: Annotated[
            str,
            Field(
                max_length=120,
                description=(
                    "Name of the human owner who checked the source and confirmed in chat. "
                    "Never an AI or model name."
                ),
            ),
        ],
    ) -> dict[str, Any]:
        """Promote one fetched observation to approved evidence. Call this only after the owner
        has seen the value and source and confirmed it in chat; reviewer is the owner's name.
        An AI or model name, or an empty reviewer, is refused and nothing is promoted."""
        request = _human_reviewer(packet_id, place_id, metric_id, reviewer)
        stored = next(
            (item for item in workspace.load() if item.packet.id == packet_id),
            None,
        )
        if stored is None:
            raise ValueError(f"research packet {packet_id} not found; call fetch_live_evidence")
        latest = stored.fetch_history[-1].result if stored.fetch_history else None
        observation = next(
            (
                item
                for item in (() if latest is None else latest.observations)
                if item.place.place_id == place_id and item.metric_id == metric_id
            ),
            None,
        )
        if observation is None:
            raise ValueError("that fetched observation is not available for this packet")
        key = (place_id, metric_id)
        if key in {
            (item.observation.place.place_id, item.observation.metric_id)
            for item in stored.promotions
        }:
            raise ValueError("that observation has already been approved for this packet")
        if key in {
            (item.place_id, item.metric_id)
            for item in stored.reviews
            if item.decision is ReviewDecision.REJECTED
        }:
            raise ValueError("that observation has already been rejected for this packet")
        with configuration() as config:
            metrics = load_metrics(config)
            promotion = promote_fetched_evidence(
                request,
                packet=stored.packet,
                observation=observation,
                metrics=metrics,
                sources=load_sources(config),
            )
        review = ReviewRecord(
            packet_id=packet_id,
            place_id=place_id,
            metric_id=metric_id,
            reviewer=promotion.reviewer,
            decision=ReviewDecision.APPROVED,
        )
        updated = stored.model_copy(
            update={
                "promotions": (*stored.promotions, promotion),
                "reviews": (*stored.reviews, review),
            }
        )
        workspace.save(updated)
        return {
            "packet_id": packet_id,
            "reviewer": promotion.reviewer,
            "approved": _observation_payload(promotion.observation, approved=True),
            "state": state_for(updated.packet, metrics, updated.promotions).value,
            "critical_metrics_needed": {
                place: list(needed)
                for place, needed in readiness_for(
                    updated.packet, metrics, updated.promotions
                ).items()
            },
        }

    def export_report(run_id: RunId) -> dict[str, Any]:
        """Return the paths of the Markdown, CSV, and SQLite reports of a compare_finalists
        run. Fails when a later run overwrote the reports; compare the finalists again then."""
        index_path = output_dir / RUN_INDEX / f"{run_id}.json"
        if not index_path.is_file():
            raise ValueError(f"no reports for run {run_id}; call compare_finalists first")
        index = RunReportIndex.model_validate_json(index_path.read_text(encoding="utf-8"))
        if index.run_id != run_id:
            raise ValueError(f"report index {index_path} names run {index.run_id}, not {run_id}")
        for key, raw in index.reports.items():
            path = Path(raw)
            if not path.is_file():
                raise FileNotFoundError(f"{key} report of run {run_id} not found: {path}")
            expected = index.sha256.get(key)
            if expected is not None and _sha256(path) != expected:
                raise ValueError(
                    f"{key} report of run {run_id} was overwritten by a later run; "
                    "call compare_finalists again"
                )
        database = Path(index.reports["database"])
        if not _database_has_run(database, run_id):
            raise ValueError(f"run {run_id} is not in its database {database}")
        return {"run_id": run_id, "reports": dict(index.reports)}

    read_only = ToolAnnotations(read_only_hint=True, open_world_hint=False)
    tools: list[tuple[ToolFunction, ToolAnnotations | None]] = [
        (find_place, None),
        (discover_towns, None),
        (compare_finalists, None),
        (explain_town, None),
        (inspect_evidence, read_only),
        (
            fetch_live_evidence,
            ToolAnnotations(
                read_only_hint=False,
                destructive_hint=False,
                idempotent_hint=False,
                open_world_hint=True,
            ),
        ),
        (
            approve_evidence,
            ToolAnnotations(
                read_only_hint=False,
                destructive_hint=True,
                idempotent_hint=False,
                open_world_hint=False,
            ),
        ),
        (export_report, read_only),
    ]
    return MCPServer(
        "lifescape",
        instructions=(
            "Lifescape helps choose a retirement town. Use find_place to get place IDs, "
            "discover_towns to find towns that feel like the owner's examples (leads only), "
            "compare_finalists to rank finalists on reviewed evidence, explain_town for "
            "the research still needed, inspect_evidence to check an evidence CSV, and "
            "export_report for a run's report files. fetch_live_evidence only proposes values: "
            "show each value and source to the owner and call approve_evidence with the "
            "owner's name only after the owner confirms it in chat; never approve as an AI. "
            "Never present a blocked town as ranked."
        ),
        tools=[_register(tool, annotations) for tool, annotations in tools],
    )


def serve(*, output_dir: Path, config_dir: Path | None = None) -> None:
    """Serve MCP over stdio until the client disconnects."""
    create_server(output_dir=output_dir, config_dir=config_dir).run("stdio")
