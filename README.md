# Lifescape

Lifescape is a local, evidence-backed retirement-town comparison tool. A person supplies a
reviewed CSV for two or more U.S. towns, sets the household decision frame, and receives an
explainable comparison with hard gates, ranked preferences, sensitivity analysis, citations, and
downloadable provenance.

The governing rule is: **gates eliminate, weights rank, evidence decides, uncertainty stays
visible.** Unknown critical evidence blocks a town. Lifescape never guesses a missing value or
produces a purchase recommendation.

## Quick start

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/), and Node.js 24.18.x.

```bash
uv sync --locked --extra dev --python 3.12
uv run playwright install chromium
npm ci
uv run lifescape app
```

The command opens a private workspace at `http://127.0.0.1:8765`. The primary version-one
journey is:

1. Set the maximum purchase budget, planning age, and household.
2. Import a reviewed evidence CSV.
3. Select at least two towns.
4. Review completeness and blocking unknowns.
5. Run the comparison.
6. Download the Markdown report, ranking CSV, sensitivity CSV, and SQLite provenance database.

The bundled dataset is synthetic and exists only to demonstrate and test the method. It must not
be used as retirement research. Hosted mode likewise accepts no inputs and shows only a finished
synthetic example.

## Evidence contract

Use `data/benchmarks/evidence.csv` as the column contract, not as real evidence. Identity and
source columns precede one column per configured metric. Blank cells remain missing. Every real
row must retain its source, geography, retrieval date, observation period, confidence, and
synthetic status.

For real comparisons, use configuration whose `research_brief.yaml` sets
`benchmark_only: false`:

```bash
lifescape run \
  --evidence path/to/reviewed-evidence.csv \
  --profile path/to/user-profile.yaml \
  --config-dir path/to/config \
  --database outputs/run.sqlite \
  --output-dir outputs/run
```

The app and CLI call the same `execute_run` authority. Tier C material cannot affect a gate or
score. Failed and unknown critical gates stay visible and unranked.

## Verification

```bash
uv run lifescape benchmark --output-dir outputs/benchmark
npm run quality:check
npm run security:check
```

The benchmark covers ten synthetic towns and must produce repeatable artifacts. Quality includes
Python tests and coverage, Ruff, mypy, browser journeys, package construction, frontend linting,
dependency audits, and secret scanning.

## Experimental research tools

The repository retains research-packet APIs, AI discovery, ACS and NOAA connectors, evidence
review and promotion, provenance auditing, and conditional research reports for continued
experimentation. They are not part of the supported version-one journey and are not required for
version-one completion.

Commercial routing, FCC broadband aggregation, property and parcel providers, neighborhood
verification, hosted accounts, remote persistence, and purchase recommendations are deferred.
Their absence does not weaken the version-one contract because reviewed evidence enters through
the same strict CSV boundary and missing critical evidence continues to block.

See the [v1 product-boundary decision](docs/decisions/ADR-v1-product-boundary.md),
[local-app specification](docs/local-app-spec.md), [source policy](docs/source-policy.md), and
[known limitations](docs/limitations.md).
