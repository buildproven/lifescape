# Changelog

## 1.0.0 — 2026-10-04

First complete release. Lifescape finds U.S. places from towns you like, helps you keep a
shortlist, and then tests finalists against reviewed evidence.

### Added

- **Place discovery.** Deterministic, explainable town matches from a packaged, hash-verified
  catalog of 32,041 U.S. places built from the 2024 Census Gazetteer and ACS 2020–2024 five-year
  files (six qualities; recommendations from the 10,215 places with 2,500 or more people).
- **Guided local app.** One-click example, one-tap styles, optional fine-tuning and limits, ten
  match cards with reasons, trade-offs, unknowns, and **Why this place?**, Keep / Not for me /
  Unsure, and a shortlist that survives reloads with backup, export, and recovery.
- **Evidence handoff.** Per-finalist evidence state, a downloadable research checklist, advanced
  CSV import, and a comparison that runs only with reviewed evidence for at least two towns.
- **Traceability.** `docs/traceability.md` links every PRD requirement to design, code, and tests,
  enforced by `tests/test_traceability.py`.
- One-command install: `uvx --from git+https://github.com/buildproven/lifescape lifescape app`.

### Changed

- The primary journey is discovery first; reviewed CSV import is an advanced path. Reports save to
  `~/Lifescape` by default.
- Experimental Claude lead discovery now drops duplicate, exemplar, and excluded towns, requires at
  least eight usable leads, records the model id, and validates packets on the model.

### Not included in 1.0

Connectors for routing and broadband, evidence contradiction tracking, scenario-to-scenario
durability comparison, hosted accounts, and the five-household usability pilot. See
`docs/limitations.md`.
