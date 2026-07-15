# Evaluation and regression safety

The golden benchmark covers the ten towns required by the master specification. Values are synthetic and encode qualitative edge cases: high cost, severe winter, coastal flood risk, and missing critical broadband evidence.

Quality gates are pytest, Ruff, strict mypy, and targeted coverage. A benchmark run verifies ten places, blocks critical failures and unknowns, exercises all 14 weighted criteria, performs 1,000 deterministic sensitivity simulations, persists SQLite tables, and emits byte-identical reports for identical inputs.

Future changes to gates, weights, normalization, or connectors should compare report CSVs and persisted gate results to identify score, rank, source, and missing-data changes.

