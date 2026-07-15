"""End-to-end Milestone 1 orchestration."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, time
from pathlib import Path

from retirement_engine.config import (
    configuration_hash,
    load_gates,
    load_metrics,
    load_sources,
    load_weights,
)
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
    simulations: int = 1000,
) -> RunResult:
    metrics = load_metrics(config_dir)
    gate_config = load_gates(config_dir)
    source_policy = load_sources(config_dir)
    weights = load_weights(config_dir)
    config_hash = configuration_hash(config_dir)
    observations = ingest_csv(evidence_path, metrics, source_policy)
    if not observations:
        raise ValueError("evidence produced no observations")
    places_by_id = {item.place.place_id: item.place for item in observations}
    place_ids = tuple(sorted(places_by_id))
    gate_results = evaluate_gates(place_ids, observations, gate_config.gates, source_policy)
    eligible = eligible_places(gate_results)
    scores = score_places(eligible, observations, metrics, weights)
    sensitivity = analyze_sensitivity(scores, simulations=simulations)
    evidence_payload = [
        observation.model_dump(mode="json")
        for observation in sorted(
            observations, key=lambda item: (item.place.place_id, item.metric_id)
        )
    ]
    digest_payload = json.dumps(
        {"config": config_hash, "evidence": evidence_payload},
        sort_keys=True,
        separators=(",", ":"),
    )
    run_id = hashlib.sha256(digest_payload.encode()).hexdigest()[:16]
    latest_retrieval = max(item.source.retrieved_at for item in observations)
    generated_at = datetime.combine(latest_retrieval, time.min, tzinfo=UTC)
    run = RunResult(
        run_id=run_id,
        config_hash=config_hash,
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
            config_hash=config_hash,
            generated_at=generated_at,
            metrics=metrics,
            gate_definitions=gate_config.gates,
            observations=observations,
            gates=gate_results,
            scores=scores,
        )
    finally:
        session.close()
        engine.dispose()
    write_reports(run, output_dir)
    return run
