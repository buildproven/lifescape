# Local app specification

## Outcome

A person can start Lifescape with one command, find towns from one or two they like, keep a
shortlist, verify finalists with reviewed evidence in a local browser, understand why towns ranked
or failed, and download the underlying reports without learning the engine's file layout or CLI
pipeline. Product requirements live in `docs/prd/lifescape-place-discovery.md`; this document
records the platform and delivery requirements and their traceability.

## Product boundary

The primary journey is discovery first (PRD FR1): Preferences → Boundaries → Matches → Shortlist →
Verify. A reviewed evidence CSV is an advanced path for people who already have a shortlist (FR15).
AI discovery, live connectors, research packets, and evidence-promotion APIs remain experimental
backend capabilities and are absent from the primary interface. The earlier CSV-only boundary
(`docs/decisions/ADR-v1-product-boundary.md`) is superseded; its evidence safeguards remain.

## Requirements

| ID | Requirement | Acceptance |
|---|---|---|
| U1 | Start the workspace with one command. | `lifescape app` serves the workspace and opens a browser by default. |
| U2 | Capture the decision frame before scoring. | The user can set maximum purchase budget, planning age, and household. Budget starts from the search's home-value limit and changes the purchase gate. |
| U3 | Control the comparison field. | The comparison field is the shortlist (or imported evidence towns); at least two towns need reviewed evidence; unknown or duplicate IDs are rejected. |
| U4 | Make evidence quality visible. | The workspace shows metric completeness and never hides missing values. Imported CSVs are classified as real, synthetic, or mixed. |
| U5 | Produce an explainable decision. | Eligible towns are ranked; failed/unknown gates remain visible; criterion and stability detail is available. |
| U6 | Preserve usable outputs. | Markdown, ranking CSV, sensitivity CSV, and SQLite provenance are written locally and downloadable. |
| S1 | Keep the app local by default. | The command binds to `127.0.0.1`; no CDN, analytics, or external runtime request is required. |
| S2 | Keep one decision implementation. | The web layer calls `execute_run`; it does not reproduce gates, scoring, sensitivity, or persistence in JavaScript. |
| S3 | Ship a complete installable artifact. | Wheel contains templates/static assets and works outside the checkout. |
| S4 | Fail visibly and safely. | Invalid files, selections, ranges, geography, dates, and source policy return actionable errors; two-place minimum and 5 MB import cap are enforced. |
| N1 | Work across common viewports and input modes. | Full journey works at desktop and mobile widths with keyboard-addressable controls and no browser console errors. |
| H1 | Offer a safe public demonstration. | Hosted mode serves only explanatory and finished-example pages; application APIs return 404. |
| H2 | Make hosted data handling explicit. | Hosted pages disclose that the example is synthetic and accept no user inputs. |
| H3 | Keep the finished example truthful. | CI traces every displayed ranking, stability result, criterion score, and blocked gate to the canonical engine benchmark. |
| H4 | Eliminate public compute exposure. | Hosted bootstrap, evidence, run, and download APIs are unavailable. |
| H5 | Explain and demonstrate the hosted product before asking for installation. | Hosted `/` explains the product and `/demo` shows a completed synthetic decision; the local app opens its workspace at `/`. |
| Q1 | Keep local and CI quality gates aligned. | `npm run quality:check` runs locked frontend, Python, coverage, browser, and package checks locally and in GitHub Actions; the package build uses the locked Hatchling backend. |
| Q2 | Reject vulnerable dependencies and leaked secrets. | npm and Python dependency audits plus Gitleaks working-tree and full-history scans run through `npm run security:check` and CI. |
| Q3 | Prevent low-quality commits and pushes. | Husky enforces conventional commits, staged formatting/linting, and full pre-push quality/security gates. |
| D1 | Run discovery offline from a verified catalog. | Catalog hash and row count are verified before use; failure returns `CATALOG_UNAVAILABLE` and leaves the evidence flow working. |
| D2 | Keep a local, recoverable shortlist. | Shortlist survives reload; malformed or unsupported saved state is backed up, never partly loaded. |
| Q4 | Make quality maturity explicit. | QA Architect configuration records production-ready maturity and required 90% coverage, tests, security, documentation, and frontend checks. |

## Design

```text
lifescape app ── opens /
   │
   ▼
FastAPI loopback server ── packaged HTML/CSS/JS workspace
   │                         │
   │  validated JSON ◀───┘   discovery: DiscoveryService (packaged catalog)
   │  bounded CSV upload
   ▼
temporary run inputs ── execute_run (existing engine)
                           │
              gates → scoring → sensitivity → SQLite/reports
                           │
                           ▼
                 explainable JSON + downloads
```

- `cli.py` exposes the one-command entry point and keeps the network host fixed to loopback.
- `web.py` owns loopback Host/Origin enforcement, exact-size raw CSV uploads with opaque
  session-local tokens, bounded request validation, evidence inspection, atomic run
  staging/publishing, response shaping, and session-scoped downloads.
- `discovery.py` owns every recommendation calculation; the browser never scores.
- `pipeline.py` remains the only decision orchestrator.
- `resources.py` resolves identical benchmark assets in editable and installed-wheel contexts.
- `templates/landing.html` explains the hosted product and routes visitors to `/demo`.
- `templates/demo.html` shows a completed, static synthetic decision.
- `templates/app.html` plus `static/` provide a dependency-free browser client; no separate Node
  build is required at runtime.

### Failure behavior

- File and engine validation failures return HTTP 422 with the concrete cause and are surfaced in
  the interface.
- Runs are staged with their own SQLite provenance database and atomically published only after all
  reports succeed; failed staging directories are removed.
- Hosted application APIs return 404 before reading inputs or invoking the engine.
- Synthetic and mixed imports retain a visible non-research warning.
- Unknown critical evidence blocks a town instead of imputing a score.
- Downloads are limited to an allowlist and the current local app session.

### Non-goals

- Automated acquisition of real evidence.
- AI-generated town facts or adapter review in the supported local journey.
- Hosted multi-user accounts or remote persistence.
- Treating the bundled synthetic benchmark as purchase research.
- Producing a purchase recommendation.

## Requirements traceability

| Requirement | Design evidence | Automated verification |
|---|---|---|
| U1 | CLI → `serve` | `tests/test_cli.py::test_cli_help_builds_all_commands`; `tests/test_user_journey.py::test_evidence_handoff_runs_only_with_reviewed_evidence`; installed command in `tests/test_packaging.py` |
| U2 | Profile controls → generated validated profile | `tests/test_user_journey.py::test_evidence_handoff_runs_only_with_reviewed_evidence`; budget/profile engine coverage in `tests/test_reports.py` |
| U3 | Town selector + `AppRunRequest` | `tests/test_web.py::test_local_app_rejects_unknown_town_selection`; `tests/test_user_journey.py::test_evidence_handoff_runs_only_with_reviewed_evidence` |
| U4 | Inspect endpoint + readiness stage | `tests/test_web.py::test_local_app_inspects_imported_evidence`; real/mixed run tests in `tests/test_web.py`; `tests/test_user_journey.py::test_advanced_evidence_import_runs_without_discovery` |
| U5 | `_response` over `RunResult` | `tests/test_web.py::test_local_app_runs_selected_towns_and_serves_reports`; `tests/test_user_journey.py::test_evidence_handoff_runs_only_with_reviewed_evidence` |
| U6 | report/SQLite allowlist + atomic run directory | `tests/test_web.py::test_local_app_runs_selected_towns_and_serves_reports`; installed benchmark in `tests/test_packaging.py` |
| S1 | fixed loopback URL; trusted Host and same-origin mutation policy; self-contained assets | `tests/test_web.py::test_local_app_rejects_hostile_host_and_origin`; browser journey asserts zero errors; static assets tested in `tests/test_packaging.py` |
| S2 | web route invokes `execute_run` | API integration and SQLite assertions in `tests/test_web.py`; engine suites under `tests/test_gates.py`, `test_scoring.py`, and `test_reports.py` |
| S3 | Hatch wheel includes web and benchmark resources | `tests/test_packaging.py::test_installed_wheel_runs_benchmark_outside_checkout` |
| S4 | transport limit + strict request models + atomic staging + ingestion policy | size and partial-failure tests in `tests/test_web.py`; `tests/test_connectors.py`; `tests/test_source_policy.py` |
| N1 | responsive CSS and semantic controls | parameterized Playwright desktop/mobile journey in `tests/test_user_journey.py::test_evidence_handoff_runs_only_with_reviewed_evidence` |
| H1 | `hosted_demo` static capability boundary | `tests/test_web.py::test_hosted_demo_is_synthetic_and_stateless`; no-JavaScript disclosure test in `tests/test_user_journey.py` |
| H2 | static synthetic disclosure and zero hosted inputs | hosted API and browser tests above |
| H3 | data attributes attached to visible demo rows | `tests/test_web.py::test_finished_demo_tracks_canonical_benchmark` |
| H4 | early hosted API 404 responses | `tests/test_web.py::test_hosted_demo_is_synthetic_and_stateless`; Vercel entry point in `api/index.py` |
| H5 | hosted explanatory `/`, completed `/demo`, and local workspace `/` | landing and finished-demo tests in `tests/test_web.py`; desktop/tablet/mobile `tests/test_user_journey.py::test_visitor_understands_product_and_opens_demo` |
| Q1 | `package.json` scripts + `.github/workflows/quality.yml` | `tests/test_quality_config.py::test_quality_automation_matches_project_contract`; full `npm run quality:check` |
| Q2 | security scripts + `.gitleaks.toml` + full-history checkout | quality-config and deleted-secret regression tests; `npm run security:check` |
| Q3 | `.husky/` hooks + commitlint/lint-staged configuration | quality-config test; commitlint smoke verification |
| D1 | `load_catalog`, `parse_catalog` | `tests/test_discovery.py::test_integrity_failures_never_load_partial_rows`; `tests/test_web.py::test_discovery_catalog_failure_degrades_only_discovery` |
| D2 | `scenario.js` | `tests/test_user_journey.py::test_discovery_recovers_from_a_malformed_saved_search`; `tests/test_user_journey.py::test_discovery_journey_from_example_town_to_recovered_shortlist` |
| Q4 | `.qualityrc.json` | quality-config test; `create-qa-architect --validate-config` |
