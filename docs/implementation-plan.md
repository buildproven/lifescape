# Milestone 1 implementation plan

This plan implements only the Core Vertical Slice defined by the Retirement Decision Engine v6 master specification. The release is local-first, deterministic, evidence-backed, and usable without external APIs.

## Acceptance path

1. Load versioned YAML requirements, user profile, gate thresholds, scoring weights, and source policy.
2. Create an immutable research run identified by a canonical configuration hash.
3. Ingest a manually curated CSV evidence bundle into SQLite while preserving source, geography, retrieval date, observation period, confidence, and synthetic status.
4. Reject prohibited source tiers and invalid evidence before it can affect gates or scores.
5. Evaluate critical town gates first. `UNKNOWN` and `FAIL` candidates cannot proceed; only `PASS` or explicitly documented `WAIVED` results are eligible.
6. Normalize eligible observations to 0–10 using declared metric directionality, winsorized candidate-set percentiles, and configurable penalties for missing non-critical metrics.
7. Rank eligible towns using weights that validate to 100 and emit raw, normalized, weighted, missing-data, confidence, and citation detail.
8. Run deterministic ±25% Monte Carlo weight perturbations with weights constrained to 100; report top-three frequency, mean rank, rank variance, and fragility.
9. Generate reproducible Markdown and CSV comparison reports, with blocking candidates, unknowns, citations, and synthetic-fixture warnings visible.
10. Exercise the complete flow against all ten specified benchmark towns and verify repeat output hashes.

## Components

- `config.py`: strict Pydantic configuration and canonical hashing.
- `models.py`: typed contracts for sources, observations, gates, scores, and sensitivity.
- `db.py`: SQLite schema and explicit persistence operations.
- `evidence.py`: manual CSV ingestion, source-quality policy, geography/freshness validation, and typed failures.
- `gates.py`: four-state gate evaluation with waiver documentation and critical missing-data blocking.
- `normalization.py` / `scoring.py`: candidate-set normalization, penalties, and weighted ranking.
- `sensitivity.py`: seeded Monte Carlo rank-stability evaluation.
- `reports.py`: deterministic Markdown and CSV artifacts.
- `cli.py`: `benchmark`, `validate-sources`, and composable run commands with structured logs and non-zero blocking failures.

## Verification gates

- Unit and integration tests cover configuration, ingestion, gates, normalization, scoring, missing data, source policy, sensitivity, reports, and repeatability.
- Coverage is at least 80% for gates, normalization, scoring, sensitivity, and source-policy logic.
- Ruff and mypy pass without suppressions.
- `retire benchmark` produces a ten-town comparison from clearly synthetic data and blocks every failed or unknown critical gate.
- Running the benchmark twice with identical inputs produces byte-identical reports.

## Deliberately deferred

Live public-data connectors, automated candidate discovery, adversarial agents, neighborhood and property analysis, mapping, scouting, web UI, and final purchase recommendations remain outside Milestone 1. The connector protocol and manual evidence boundary are designed so Milestone 2 can add public sources without changing gate or scoring semantics.
