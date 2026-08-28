# CLAUDE.md

## Purpose

This repository implements local-first place discovery and evidence-backed retirement location
decisions. The approved product source of truth is
`docs/prd/lifescape-place-discovery.md`. Every product issue and PR must cite a specific PRD goal,
requirement, or acceptance criterion. Preserve the governing rule: preferences and examples
discover; gates eliminate; weights rank; evidence decides; uncertainty remains visible.

## Development

- Install: `.venv/bin/pip install -e ".[dev]"`
- Test: `.venv/bin/pytest`
- Lint: `.venv/bin/ruff check .`
- Types: `.venv/bin/mypy src`
- Benchmark: `.venv/bin/lifescape benchmark --output-dir outputs/benchmark`

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
and the benchmark before shipping.
