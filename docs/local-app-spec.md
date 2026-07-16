# Local app specification

## Outcome

A person can start Lifescape with one command, complete a guided retirement-town comparison in a
local browser, understand why towns ranked or failed, and download the underlying reports without
learning the engine's file layout or CLI pipeline.

## Requirements

| ID | Requirement | Acceptance |
|---|---|---|
| U1 | Start the workspace with one command. | `retire app` serves the workspace and opens a browser by default. |
| U2 | Capture the decision frame before scoring. | The user can set maximum purchase budget, planning age, and household. Budget changes the purchase gate. |
| U3 | Control the comparison field. | The user can search and select two or more towns; unknown or duplicate IDs are rejected. |
| U4 | Make evidence quality visible. | The workspace shows metric completeness and never hides missing values. Imported CSVs are classified as real, synthetic, or mixed. |
| U5 | Produce an explainable decision. | Eligible towns are ranked; failed/unknown gates remain visible; criterion and stability detail is available. |
| U6 | Preserve usable outputs. | Markdown, ranking CSV, sensitivity CSV, and SQLite provenance are written locally and downloadable. |
| S1 | Keep the app local by default. | The command binds to `127.0.0.1`; no CDN, analytics, or external runtime request is required. |
| S2 | Keep one decision implementation. | The web layer calls `execute_run`; it does not reproduce gates, scoring, sensitivity, or persistence in JavaScript. |
| S3 | Ship a complete installable artifact. | Wheel contains templates/static assets and works outside the checkout. |
| S4 | Fail visibly and safely. | Invalid files, selections, ranges, geography, dates, and source policy return actionable errors; two-place minimum and 5 MB import cap are enforced. |
| N1 | Work across common viewports and input modes. | Full journey works at desktop and mobile widths with keyboard-addressable controls and no browser console errors. |
| H1 | Offer a safe public demonstration. | Hosted mode uses only bundled synthetic evidence and rejects CSV imports. |
| H2 | Make hosted data handling and persistence limits explicit. | Hosted mode discloses that selected constraints are processed temporarily, creates no durable run directory, and offers no report or SQLite download. |
| H3 | Preserve one product implementation. | Hosted mode uses the same UI and `execute_run` engine as the local app. |
| H4 | Bound and disable public compute safely. | Hosted runs require a same-origin browser request, fail closed on deployment, enforce bounded per-process safeguards, use a deployment-wide Vercel edge limit, and have a disabled emergency-deny rule ready for immediate publication. |
| H5 | Explain the product before asking a hosted visitor to operate it. | `/` states the problem, method, output, synthetic-data limitation, and local/private path; its primary action opens the shared workspace at `/demo`. |
| Q1 | Keep local and CI quality gates aligned. | `npm run quality:check` runs locked frontend, Python, coverage, browser, and package checks locally and in GitHub Actions; the package build uses the locked Hatchling backend. |
| Q2 | Reject vulnerable dependencies and leaked secrets. | npm and Python dependency audits plus Gitleaks working-tree and full-history scans run through `npm run security:check` and CI. |
| Q3 | Prevent low-quality commits and pushes. | Husky enforces conventional commits, staged formatting/linting, and full pre-push quality/security gates. |
| Q4 | Make quality maturity explicit. | QA Architect configuration records production-ready maturity and required 90% coverage, tests, security, documentation, and frontend checks. |

## Design

```text
retire app ── opens /demo
   │
   ▼
FastAPI loopback server ── packaged HTML/CSS/JS workspace
   │                         │
   │  bounded CSV upload + validated JSON ◀───┘
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
- `pipeline.py` remains the only decision orchestrator.
- `resources.py` resolves identical benchmark assets in editable and installed-wheel contexts.
- `templates/landing.html` explains the hosted product and routes visitors to `/demo`.
- `templates/app.html` plus `static/` provide a dependency-free browser client; no separate Node
  build is required at runtime.

### Failure behavior

- File and engine validation failures return HTTP 422 with the concrete cause and are surfaced in
  the interface.
- Runs are staged with their own SQLite provenance database and atomically published only after all
  reports succeed; failed staging directories are removed.
- Hosted runs are disabled unless `LIFESCAPE_HOSTED_RUNS_ENABLED=true`; enabled deployments apply
  same-origin, bounded per-process safeguards and a verified deployment-wide Vercel edge limit
  before invoking the engine. A published, disabled emergency-deny rule provides the immediate
  incident switch.
- Synthetic and mixed imports retain a visible non-research warning.
- Unknown critical evidence blocks a town instead of imputing a score.
- Downloads are limited to an allowlist and the current local app session.

### Non-goals

- Automated acquisition of real evidence.
- Hosted multi-user accounts or remote persistence.
- Treating the bundled synthetic benchmark as purchase research.

## Requirements traceability

| Requirement | Design evidence | Automated verification |
|---|---|---|
| U1 | CLI → `serve` | `tests/test_cli.py::test_cli_help_builds_all_commands`; `tests/test_user_journey.py::test_user_completes_guided_comparison`; installed command in `tests/test_packaging.py` |
| U2 | Profile controls → generated validated profile | `tests/test_user_journey.py::test_user_completes_guided_comparison`; budget/profile engine coverage in `tests/test_reports.py` |
| U3 | Town selector + `AppRunRequest` | `tests/test_web.py::test_local_app_rejects_unknown_town_selection`; `tests/test_user_journey.py::test_user_completes_guided_comparison` |
| U4 | Inspect endpoint + readiness stage | `tests/test_web.py::test_local_app_inspects_imported_evidence`; real/mixed run tests in `tests/test_web.py`; `tests/test_user_journey.py::test_user_keeps_mixed_evidence_warning_after_scoring` |
| U5 | `_response` over `RunResult` | `tests/test_web.py::test_local_app_runs_selected_towns_and_serves_reports`; `tests/test_user_journey.py::test_user_completes_guided_comparison` |
| U6 | report/SQLite allowlist + atomic run directory | `tests/test_web.py::test_local_app_runs_selected_towns_and_serves_reports`; installed benchmark in `tests/test_packaging.py` |
| S1 | fixed loopback URL; trusted Host and same-origin mutation policy; self-contained assets | `tests/test_web.py::test_local_app_rejects_hostile_host_and_origin`; browser journey asserts zero errors; static assets tested in `tests/test_packaging.py` |
| S2 | web route invokes `execute_run` | API integration and SQLite assertions in `tests/test_web.py`; engine suites under `tests/test_gates.py`, `test_scoring.py`, and `test_reports.py` |
| S3 | Hatch wheel includes web and benchmark resources | `tests/test_packaging.py::test_installed_wheel_runs_benchmark_outside_checkout` |
| S4 | transport limit + strict request models + atomic staging + ingestion policy | size and partial-failure tests in `tests/test_web.py`; `tests/test_connectors.py`; `tests/test_source_policy.py` |
| N1 | responsive CSS and semantic controls | parameterized Playwright desktop/mobile journey in `tests/test_user_journey.py::test_user_completes_guided_comparison` |
| H1 | `hosted_demo` capability boundary + hidden import control | `tests/test_web.py::test_hosted_demo_is_synthetic_and_stateless`; `tests/test_user_journey.py::test_hosted_user_completes_synthetic_demo_without_private_controls` |
| H2 | temporary staging without publication + empty downloads | hosted API and browser tests above |
| H3 | same `create_app`, browser assets, and `execute_run` path | hosted API integration test; Vercel entry point in `api/index.py` |
| H4 | `HostedRunGuard`, required Origin, fail-closed deployment switch, live rate-limit rule, and disabled emergency-deny rule | hosted API and bounded-state tests in `tests/test_web.py`; `ops/vercel-firewall.json`; `npm run ops:verify:vercel` |
| H5 | explanatory `/` route + shared `/demo` route | `tests/test_web.py::test_landing_page_explains_the_product_and_links_to_demo`; desktop/mobile `tests/test_user_journey.py::test_visitor_understands_product_and_opens_demo` |
| Q1 | `package.json` scripts + `.github/workflows/quality.yml` | `tests/test_quality_config.py::test_quality_automation_matches_project_contract`; full `npm run quality:check` |
| Q2 | security scripts + `.gitleaks.toml` + full-history checkout | quality-config and deleted-secret regression tests; `npm run security:check` |
| Q3 | `.husky/` hooks + commitlint/lint-staged configuration | quality-config test; commitlint smoke verification |
| Q4 | `.qualityrc.json` | quality-config test; `create-qa-architect --validate-config` |
