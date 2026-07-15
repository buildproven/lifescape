"""End-to-end Milestone 1 orchestration."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, time
from pathlib import Path

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
) -> RunResult:
    config = load_configuration(config_dir, profile_path)
    observations = ingest_csv(evidence_path, config.metrics, config.sources)
    if not observations:
        raise ValueError("evidence produced no observations")
    if config.research_brief.benchmark_only and any(
        not observation.source.synthetic for observation in observations
    ):
        raise ValueError("benchmark-only research brief cannot process non-synthetic evidence")
    places_by_id = {item.place.place_id: item.place for item in observations}
    place_ids = tuple(sorted(places_by_id))
    gate_results = evaluate_gates(place_ids, observations, config.gates.gates, config.sources)
    eligible = eligible_places(gate_results)
    scores = score_places(eligible, observations, config.metrics, config.weights)
    sensitivity = analyze_sensitivity(scores, simulations=simulations)
    evidence_payload = [
        observation.model_dump(mode="json")
        for observation in sorted(
            observations, key=lambda item: (item.place.place_id, item.metric_id)
        )
    ]
    digest_payload = json.dumps(
        {"config": config.config_hash, "evidence": evidence_payload},
        sort_keys=True,
        separators=(",", ":"),
    )
    run_id = hashlib.sha256(digest_payload.encode()).hexdigest()[:16]
    latest_retrieval = max(item.source.retrieved_at for item in observations)
    generated_at = datetime.combine(latest_retrieval, time.min, tzinfo=UTC)
    run = RunResult(
        run_id=run_id,
        profile_version=config.user_profile.profile_version,
        config_hash=config.config_hash,
        generated_at=generated_at,
        places=tuple(places_by_id[place_id] for place_id in place_ids),
        observations=observations,
        gate_results=gate_results,
        scores=scores,
        sensitivity=sensitivity,
    )
    session, engine = initialize_database(database_path)
    try:
        persist_run(
            session,
            run_id=run_id,
            profile_version=config.user_profile.profile_version,
            config_hash=config.config_hash,
            generated_at=generated_at,
            metrics=config.metrics,
            gate_definitions=config.gates.gates,
            observations=observations,
            gates=gate_results,
            scores=scores,
        )
    finally:
        session.close()
        engine.dispose()
    write_reports(run, output_dir)
    return run
