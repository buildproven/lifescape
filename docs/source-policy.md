# Source policy

- Tier A official evidence and Tier B approved secondary evidence may affect scores.
- Tier C material is discovery-only and is rejected before ingestion into the decision path.
- Gates require high-confidence evidence by default.
- Source URL or file reference, publisher, retrieval date, observation period, effective observation date, confidence, geography, and synthetic status are preserved.
- Source and place geography must match exactly in Milestone 1; no silent geography substitution is permitted.
- Non-synthetic sources older than the configured retrieval-age maximum are rejected, and each observation must independently satisfy its metric's freshness window.
- Synthetic benchmark evidence is always labeled and must never be represented as research.

Perplexity, forums, tourism sites, real-estate blogs, ranking sites, and AI summaries are Tier C. A primary or approved secondary source must independently verify a discovered claim before it can enter a score or gate.
