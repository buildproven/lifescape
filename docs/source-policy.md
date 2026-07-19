# Source policy

- Tier A official evidence and Tier B approved secondary evidence may affect scores.
- Tier C material is discovery-only and is rejected before ingestion into the decision path.
- Gates require high-confidence evidence by default.
- Source URL or file reference, publisher, retrieval date, observation period, effective observation date, confidence, geography, and synthetic status are preserved.
- Source and place geography must match exactly in Milestone 1; no silent geography substitution is permitted.
- Non-synthetic sources older than the configured retrieval-age maximum are rejected, and each observation must independently satisfy its metric's freshness window.
- Synthetic benchmark evidence is always labeled and must never be represented as research.

Perplexity, forums, tourism sites, real-estate blogs, ranking sites, and AI summaries are Tier C. A primary or approved secondary source must independently verify a discovered claim before it can enter a score or gate.

## Locally derived composite metrics

A metric can be computed by this codebase from multiple Tier A/B component values (for example, `distress_index`: an unweighted average of three Census ACS rates — see `lifescape.connectors.census_acs`). The tier and confidence of such a metric reflect its component evidence, not the combination formula: the underlying data is genuinely official, but the *formula* that combines it into a single value is this project's own construction, with no established methodology, external validation, or peer review. `SourceRecord.title` on any such observation must say explicitly that the value is derived and not an official statistic. A reader of a comparison report should not treat a locally derived composite's PASS/FAIL on a gate as carrying the same evidentiary weight as a directly sourced official statistic, even though both currently satisfy the same tier/confidence thresholds.
