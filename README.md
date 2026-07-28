# Lifescape

Local-first, evidence-backed retirement planning for comparing U.S. towns. Lifescape turns
non-negotiables, sourced town evidence, and future-life priorities into an explainable shortlist.
Its decision engine provides strict YAML configuration, manual CSV evidence ingestion, SQLite
provenance, hard gates, normalized scoring, Monte Carlo sensitivity, source-quality enforcement,
and reproducible reports.

The governing rule is: **gates eliminate, weights rank, evidence decides, uncertainty stays visible.** Tier C discovery material cannot affect a gate or score, and unknown critical gates block a candidate.

## Quick start

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/), and Node.js 22.9+.

```bash
uv sync --locked --extra dev --python 3.12
uv run playwright install chromium
npm ci
uv run lifescape app
```

`lifescape app` opens a guided local workspace at `http://127.0.0.1:8765`. Set your
budget and planning age, choose towns, review evidence completeness, then run and
download the comparison. You can also import a CSV matching the documented evidence
contract; the file is processed locally and validated before it reaches scoring.

The public deployment at `lifescape.buildproven.ai` is a static explanation and finished
synthetic example. It exposes no comparison API, accepts no inputs, and stores no user data.
Use the local app for your own evidence, computation, reports, and downloadable provenance.

For the command-line benchmark and QA Architect checks:

```bash
uv run lifescape benchmark --output-dir outputs/benchmark
npm run quality:check
npm run security:check
```

The installed `lifescape benchmark` command includes its synthetic evidence and default configuration,
so it works from any directory. Pass `--config-dir` only to exercise a custom configuration.

The benchmark data is synthetic and exists only to exercise methodology. Generated artifacts include `comparison.md`, `comparison.csv`, and `sensitivity.csv`.

## Manual evidence

Use the benchmark CSV as the import contract. Identity and source columns, including an explicit `observed_at` date, precede one column per configured metric. Blank metric cells remain missing; they are never guessed.

For real evidence, use a config directory whose `research_brief.yaml` sets `benchmark_only: false`.

```bash
lifescape run \
  --evidence path/to/evidence.csv \
  --profile path/to/user_profile.yaml \
  --config-dir config \
  --database outputs/run.sqlite \
  --output-dir outputs/run
```

See [the implementation plan](docs/implementation-plan.md), [source policy](docs/source-policy.md), and [limitations](docs/limitations.md).

### Audit manual provenance

The wide CSV contract has one row-level source block. Before using a manually researched
CSV for a decision, audit every populated metric against a separate metric-specific evidence
manifest. The audit does not modify, infer, or score evidence; it writes a deterministic JSON
ledger and a blank correction template.

```bash
lifescape audit-evidence \
  --evidence path/to/evidence.csv \
  --config-dir config \
  --output-dir outputs/evidence-audit
```

Populate the generated `evidence-manifest-template.csv` with the source that supports each
individual metric, then re-run with `--manifest path/to/evidence-manifest.csv`. A record whose
source, geography, freshness, confidence, or metric semantics cannot be validated remains
`action_required` and never changes the strict scoring path.

### Produce a conditional research shortlist

For early-stage discovery, produce a bucketed research queue alongside the unchanged strict
engine output. This command never ranks towns or treats missing critical evidence as a pass.
Use `--investigate-place` only for town IDs that a researcher has deliberately selected as
leads; repeat the option to include more than one lead.

```bash
lifescape research-report \
  --evidence path/to/evidence.csv \
  --profile path/to/user_profile.yaml \
  --config-dir config \
  --manifest path/to/evidence-manifest.csv \
  --investigate-place lake_geneva_wi \
  --output-dir outputs/research-report
```

The output contains three explicit buckets: **Investigate now**, **Known reject**, and
**Insufficient evidence**. A town can be a known reject only when its failed gate has
metric-specific provenance that passed the audit. All other unresolved critical evidence is
listed with its next verification action.

## Live snowfall evidence

`lifescape live-run` can additionally fetch NOAA NCEI Global Summary of the Year
(GSOY) snowfall. Because a weather station is not a town-wide aggregate, the
station selection is explicit in the places YAML and remains visible in source
provenance. Use a completed calendar year and a station you have independently
determined represents the town; Lifescape neither finds a nearest station nor
combines stations or years.

```yaml
lake_geneva_wi:
  name: Lake Geneva
  state: WI
  census_acs: "55:43075"
  noaa_gsoy: "USC00218450:2024"
```

The NOAA observation is GSOY's direct `SNOW` total in inches for that exact
station-year. Empty, flagged, malformed, or unavailable station records produce
missing evidence, so the critical winter gate stays UNKNOWN and blocks the
candidate instead of receiving an inferred value.
