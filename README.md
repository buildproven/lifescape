# Retirement Decision Engine

A local-first, evidence-backed engine for comparing U.S. retirement towns. Milestone 1 implements a tested vertical slice: strict YAML configuration, manual CSV evidence ingestion, SQLite provenance, hard gates, normalized scoring, Monte Carlo sensitivity, source-quality enforcement, and reproducible reports.

The governing rule is: **gates eliminate, weights rank, evidence decides, uncertainty stays visible.** Tier C discovery material cannot affect a gate or score, and unknown critical gates block a candidate.

## Quick start

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --locked --extra dev --python 3.12
uv run playwright install chromium
uv run retire app
```

`retire app` opens a guided local workspace at `http://127.0.0.1:8765`. Set your
budget and planning age, choose towns, review evidence completeness, then run and
download the comparison. You can also import a CSV matching the documented evidence
contract; the file is processed locally and validated before it reaches scoring.

The public deployment at `lifescape.buildproven.ai` is intentionally a stateless
synthetic-data demonstration. It does not accept private CSV imports or promise durable
reports; use the local app for private evidence and downloadable provenance.

For the command-line benchmark and developer checks:

```bash
uv run retire benchmark --output-dir outputs/benchmark
uv run pytest
uv run ruff check .
uv run mypy src
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
