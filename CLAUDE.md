# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Purpose

This repository implements local-first place discovery and evidence-backed retirement location
decisions. The approved product source of truth is
`docs/prd/lifescape-place-discovery.md`. Every product issue and PR must cite a specific PRD goal,
requirement, or acceptance criterion. Preserve the governing rule: preferences and examples
discover; gates eliminate; weights rank; evidence decides; uncertainty remains visible.

## Commands

Python 3.12 with `uv` (`make install` runs `uv sync`, installs Playwright Chromium, and `npm ci`).
A plain `.venv/bin/...` also works (`.venv/bin/pip install -e ".[dev]"`).

- Test: `.venv/bin/pytest` (single test: `.venv/bin/pytest tests/test_gates.py::test_name`)
- Lint / types: `.venv/bin/ruff check .`, `.venv/bin/mypy src` (strict)
- Benchmark: `.venv/bin/lifescape benchmark --output-dir outputs/benchmark`
- Run the app: `lifescape app` (FastAPI, default port 8765)
- Full gate: `npm run quality:check` (prettier, eslint, stylelint, ruff format, mypy, pytest with
  90% coverage floor, `uv build`). Frontend only: `npm run quality:frontend`.
- Rebuild the packaged place catalog: `python scripts/build_place_catalog.py` (`--verify` rebuilds
  and compares against the committed hash).
- Run `tests/test_user_journey.py` (Playwright) on its own. Concurrent pytest runs cause timeouts.
- `tests/test_quality_config.py` runs commitlint and needs `node_modules` (symlink it in worktrees).
- `main` requires the `quality` check and an up-to-date branch (`gh pr update-branch` when behind).

## Architecture

Two separate engines share one app. Do not let one leak into the other.

**Discovery** (`src/lifescape/discovery.py`): ranks ~10k U.S. places from a packaged, hash-verified
catalog (`src/lifescape/data/place-catalog.csv.gz` + `.manifest.json`) using Census ACS and NOAA
1991–2020 climate normals. `SCORED_FIELDS` defines the comparable qualities; normalization is
winsorized min-max with 5th/95th percentile bounds stored in the manifest. Climate values come from
the nearest station within 30 miles and carry `climate_sources` provenance; missing values stay
null, never imputed. Discovery is Tier C: it cannot affect a gate or score in the evidence engine.
Adding a field means touching `SCORED_FIELDS`, `CATALOG_COLUMNS`, the build script's
`FIELD_DEFINITIONS`, `STYLES`/labels in `static/app.js`, the data dictionary, and the test fixtures
(which pad rows to the column count).

**Evidence pipeline** (`pipeline.py` orchestrates): `config.py` (strict YAML from `config/`) →
`connectors/` and `research*.py` (sources under the source policy) → `normalization.py` →
`gates.py` → `scoring.py` → `sensitivity.py` → `reports.py` / `db.py` (SQLite) with provenance and
observation dates preserved. `evidence.py` / `evidence_audit.py` validate reviewed evidence;
the discovery shortlist hands off to this pipeline only with reviewed evidence.

**Web** (`web.py` + `templates/app.html` + `static/app.js`/`app.css`): vanilla JS, no framework.
`api/index.py` is the Vercel entry. CLI commands live in `cli.py`.

## Invariants

- Never invent or silently impute evidence.
- Unknown critical gates block a candidate.
- Tier C discovery material cannot affect a gate or score.
- Preserve source provenance, observation dates, missing values, and
  reproducibility in reports and SQLite output.
- Benchmark data is synthetic and must not be represented as real-world
  evidence.
- Keep configuration validation strict and maintain Python 3.12 compatibility.

## Review

Methodology changes require tests covering gates, normalization, scoring,
sensitivity, source policy, and missing-data behavior. Run tests, Ruff, mypy,
and the benchmark before shipping. Product backlog lives in the BuildProven Linear project
`lifescape` (key BUI); PRs say `Closes BUI-XXX`.
