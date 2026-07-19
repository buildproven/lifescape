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
- `lifescape benchmark` produces a ten-town comparison from clearly synthetic data and blocks every failed or unknown critical gate.
- Running the benchmark twice with identical inputs produces byte-identical reports.

## Deliberately deferred

Live public-data connectors, automated candidate discovery, adversarial agents, neighborhood and property analysis, mapping, scouting, web UI, and final purchase recommendations remain outside Milestone 1. The connector protocol and manual evidence boundary are designed so Milestone 2 can add public sources without changing gate or scoring semantics.

Milestone 2 has begun: `lifescape.connectors.census_acs.CensusAcsConnector` implements the `Connector` protocol against the Census ACS 5-Year Data Profile API, requiring a free `CENSUS_API_KEY` (see `.env.example`). It supports two metrics: `education_attainment` (a direct pull-through of `DP02_0068PE`) and `distress_index` (a derived proxy: the unweighted average of poverty rate `DP03_0128PE`, unemployment rate `DP03_0009PE`, and vacant housing rate `DP04_0003PE` — documented as non-official in the observation's `SourceRecord.title` so it is never mistaken for a published Census statistic). `distress_index` is a critical gate (`distress_profile`, threshold `<=7`), so this is the first metric where live-fetched evidence can actually block a candidate, not just influence scoring.

`lifescape.connectors.orchestrate.fetch_live_observations` runs a set of connectors against a `place_id -> "state_fips:place_fips"` mapping and folds any per-place connector failure or validation failure into missing evidence rather than aborting — `gates.evaluate_gates` already treats a missing observation as `UNKNOWN` and blocks the place, so connector failures need no special-cased handling. `pipeline.execute_run` additionally rejects a run where a manual and a live observation share a `place_id` but disagree on the rest of the `PlaceRecord` (name/state), mirroring `evidence.ingest_csv`'s existing identity guard. `lifescape live-run` wires this into the pipeline: it fetches live observations, merges them with a manual evidence CSV (manual rows always win on a `(place, metric)` conflict), and uses `config/research_brief.live.yaml` (`benchmark_only: false`) instead of the benchmark's synthetic-only brief.

Researched but not pursued: `median_sale_price`, `flood_risk_score`, and `one_level_inventory_count` have no free, town-level public API as of 2026-07 (Zillow's public API is discontinued, FEMA's flood API requires a paid third-party wrapper, and listing inventory is commercial/MLS-adjacent data) — these stay manual/CSV-sourced. `broadband_mbps_down` has a real source (FCC Broadband Map API) but needs a separate signup token and its place-level aggregation behavior is unverified; lower priority than the metrics already covered.

Remaining Milestone 2 work: connectors for `broadband_mbps_down`, `annual_snowfall` (NOAA NCEI Climate Data Online — verified live and keyless, needs a station-lookup step), `er_drive_minutes` (CMS Hospital General Information dataset — verified live and keyless, needs address-to-drive-time routing), and automated candidate discovery. `ACS_YEAR` is still hardcoded to 2023 and must be bumped on each ACS release, and the state-FIPS lookup only covers the states in the current benchmark set — both should be revisited before relying on `live-run` for real evidence.
