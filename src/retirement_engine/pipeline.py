"""End-to-end Milestone 1 orchestration."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, time
from pathlib import Path

from retirement_engine import __version__
from retirement_engine.config import load_configuration
from retirement_engine.db import initialize_database, persist_run
from retirement_engine.evidence import ingest_csv
from retirement_engine.gates import eligible_places, evaluate_gates
from retirement_engine.models import RunResult
from retirement_engine.reports import write_reports
from retirement_engine.scoring import score_places
from retirement_engine.sensitivity import analyze_sensitivity


def execute_run(
    *,
    evidence_path: Path,
    config_dir: Path,
    database_path: Path,
    output_dir: Path,
    profile_path: Path | None = None,
    simulations: int = 1000,
    sensitivity_seed: int = 20260714,
    as_of: date | None = None,
) -> RunResult:
    config = load_configuration(config_dir, profile_path)
    raw_observations = ingest_csv(
        evidence_path,
        config.metrics,
        config.sources,
        as_of=as_of,
        required_scope=config.research_brief.scope,
    )
    effective_as_of = as_of or (
        max(item.source.retrieved_at for item in raw_observations)
        if raw_observations and all(item.source.synthetic for item in raw_observations)
        else date.today()
    )
    observations = raw_observations
    if not observations:
        raise ValueError("evidence produced no observations")
    selected_regions = {
        region.id: region
        for region in config.regions.regions
        if region.id in config.research_brief.regions
    }
    state_scopes = [region.states for region in selected_regions.values()]
    if "*" not in state_scopes:
        allowed_states = {
            state.upper()
            for states in state_scopes
            if not isinstance(states, str)
            for state in states
        }
        out_of_scope = sorted(
            {
                f"{item.place.place_id} ({item.place.state})"
                for item in observations
                if item.place.state.upper() not in allowed_states
            }
        )
        if out_of_scope:
            raise ValueError(f"evidence contains places outside configured regions: {out_of_scope}")
    if config.research_brief.benchmark_only and any(
        not observation.source.synthetic for observation in observations
    ):
        raise ValueError("benchmark-only research brief cannot process non-synthetic evidence")
    places_by_id = {item.place.place_id: item.place for item in observations}
    place_ids = tuple(sorted(places_by_id))
    effective_gates = tuple(
        gate.model_copy(
            update={"threshold": min(gate.threshold, config.user_profile.purchase_budget_max)}
        )
        if gate.id == "purchase_feasibility"
        else gate
        for gate in config.gates.gates
    )
    gate_results = evaluate_gates(
        place_ids,
        observations,
        effective_gates,
        config.sources,
        as_of=effective_as_of,
    )
    eligible = eligible_places(gate_results)
    scores = score_places(eligible, observations, config.metrics, config.weights)
    sensitivity = analyze_sensitivity(scores, simulations=simulations, seed=sensitivity_seed)
    evidence_payload = [
        observation.model_dump(mode="json")
        for observation in sorted(
            observations, key=lambda item: (item.place.place_id, item.metric_id)
        )
    ]
    digest_payload = json.dumps(
        {
            "config": config.config_hash,
            "engine_version": __version__,
            "evaluated_as_of": effective_as_of.isoformat(),
            "evidence": evidence_payload,
            "sensitivity_seed": sensitivity_seed,
            "simulations": simulations,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    run_id = hashlib.sha256(digest_payload.encode()).hexdigest()[:16]
    latest_retrieval = max(item.source.retrieved_at for item in observations)
    evidence_through = datetime.combine(latest_retrieval, time.min, tzinfo=UTC)
    run = RunResult(
        run_id=run_id,
        profile_version=config.user_profile.profile_version,
        config_hash=config.config_hash,
        engine_version=__version__,
        evaluated_as_of=effective_as_of,
        evidence_through=evidence_through,
        simulations=simulations,
        sensitivity_seed=sensitivity_seed,
        places=tuple(places_by_id[place_id] for place_id in place_ids),
        observations=observations,
        gate_results=gate_results,
        scores=scores,
        sensitivity=sensitivity,
    )
    session, engine = initialize_database(database_path)
    try:
        persisted = persist_run(
            session,
            run_id=run_id,
            profile_version=config.user_profile.profile_version,
            config_hash=config.config_hash,
            engine_version=__version__,
            evaluated_as_of=effective_as_of,
            evidence_through=evidence_through,
            simulations=simulations,
            sensitivity_seed=sensitivity_seed,
            metrics=config.metrics,
            gate_definitions=effective_gates,
            observations=observations,
            gates=gate_results,
            scores=scores,
            sensitivity=sensitivity,
        )
    finally:
        session.close()
        engine.dispose()
    run = run.model_copy(update={"persisted": persisted})
    write_reports(run, output_dir)
    return run
