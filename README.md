# Retirement Decision Engine

A local-first, evidence-backed engine for comparing U.S. retirement towns. Milestone 1 implements a tested vertical slice: strict YAML configuration, manual CSV evidence ingestion, SQLite provenance, hard gates, normalized scoring, Monte Carlo sensitivity, source-quality enforcement, and reproducible reports.

The governing rule is: **gates eliminate, weights rank, evidence decides, uncertainty stays visible.** Tier C discovery material cannot affect a gate or score, and unknown critical gates block a candidate.

## Quick start

Requires Python 3.12+.

```bash
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/retire benchmark --output-dir outputs/benchmark
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/mypy src
```

The installed `retire benchmark` command includes its synthetic evidence and default configuration,
so it works from any directory. Pass `--config-dir` only to exercise a custom configuration.

The benchmark data is synthetic and exists only to exercise methodology. Generated artifacts include `comparison.md`, `comparison.csv`, and `sensitivity.csv`.

## Manual evidence

Use the benchmark CSV as the import contract. Identity and source columns, including an explicit `observed_at` date, precede one column per configured metric. Blank metric cells remain missing; they are never guessed.

For real evidence, use a config directory whose `research_brief.yaml` sets `benchmark_only: false`.

```bash
retire run \
  --evidence path/to/evidence.csv \
  --profile path/to/user_profile.yaml \
  --config-dir config \
  --database outputs/run.sqlite \
  --output-dir outputs/run
```

See [the implementation plan](docs/implementation-plan.md), [source policy](docs/source-policy.md), and [limitations](docs/limitations.md).
