# Proposal: Agent-first and CLI-first Lifescape

> Status: proposed. This plan does not amend the approved product boundary.
> PRD trace: G1-G5, FR1-FR16, Reproducibility, Privacy, and Evidence integrity in
> `docs/prd/lifescape-place-discovery.md`.

## Decision

Make Lifescape **agent-ready and CLI-first**, but not agent-authoritative. A local agent can
operate the complete discovery, shortlist, research, and verification workflow through a stable,
typed command interface. It can suggest candidates and research tasks. It cannot make an evidence
claim, approve evidence, clear a gate, change a score, or make a relocation recommendation.

The browser remains a guided view of the same local services. It is not the only way to use the
product. The deterministic discovery catalog and `execute_run` remain the decision authorities.

This is an implementation direction for BUI-334. It needs an approved PRD amendment before an AI
assistant becomes a default product entrance or before any model/provider receives user data.

## Why this shape

Lifescape already has the correct safety seam:

```text
agent or browser input
        |
        v
typed local commands and domain services
        |
        +--> deterministic discovery -> local shortlist
        |
        +--> advisory research packet (Tier C)
        |          |
        |          v
        |       named human review + A/B provenance
        |          |
        v          v
reviewed evidence CSV -> execute_run -> gates, ranking, sensitivity, reports
```

The current package has a sound evidence CLI (`run`, `audit-evidence`, `research-report`,
`benchmark`) and an experimental local research API. It does not yet expose the approved discovery
and shortlist journey, so an external agent would have to drive browser state or reconstruct
business logic. That is the gap this plan closes.

## Product roles

### 1. Deterministic product agent: first delivery target

A local agent receives a user request and calls documented commands. It can:

- translate plain language into a proposed `SearchProfile` and show the resolved structured input;
- call deterministic lookup and recommendation commands;
- explain returned components, trade-offs, unknowns, and catalog dates only from returned fields;
- save explicit user keep/reject/unsure decisions in a local scenario;
- create a research packet and list missing verification work; and
- run an admissible evidence comparison and summarize its generated artifacts.

The agent must ask the user to confirm any profile, shortlist decision, evidence-review decision,
or command that writes durable local state. It must identify discovery output as advisory and
evidence output as the only decision input.

### 2. Optional model-assisted research agent: later, opt-in only

An enabled provider may turn an approved `SearchBrief` into Tier C leads and source leads. It must
write a versioned `ResearchPacket`, including model/provider identity, prompt-template version,
packet timestamp, source URLs, caveats, and raw response checksum. It cannot write an
`ObservationRecord`, an evidence CSV, or a review decision.

This is a research accelerator, not the product's recommendation engine. The default discovery
path stays the packaged, deterministic catalog required by FR4-FR9.

## Stable machine interface

Add an `agent` command group after the discovery domain exists. It is a thin adapter over domain
services; it must not call FastAPI through loopback or duplicate validation in Typer.

| Capability | Proposed command | Durable result |
| --- | --- | --- |
| Inspect capabilities and contract version | `lifescape agent capabilities --format json` | Versioned capability document |
| Validate/normalize a search profile | `lifescape agent discovery validate --profile profile.json` | Canonical profile or typed validation errors |
| Lookup a place | `lifescape agent discovery lookup --query QUERY` | Catalog identity and eligibility |
| Get recommendations | `lifescape agent discovery recommend --profile profile.json` | Deterministic `DiscoveryResult` |
| Create or inspect a local scenario | `lifescape agent scenario create|show` | Versioned local scenario JSON |
| Record an explicit shortlist decision | `lifescape agent scenario decide --place ID --decision keep|reject|unsure` | Updated scenario and audit event |
| Hand off two or more towns | `lifescape agent evidence readiness --scenario FILE` | Missing/verified/blocked metric matrix |
| Create/inspect/select a research packet | `lifescape agent research create|show|select` | Local packet JSON and checksum |
| Fetch proposed observations | `lifescape agent research fetch --packet FILE` | Review-pending snapshot only |
| Human-only evidence review | `lifescape agent research approve|reject ... --reviewer NAME` | Signed local review record |
| Export approved evidence / evaluate | `lifescape agent research export` and existing `lifescape run` | CSV, SQLite, reports, run ID |
| Verify an artifact set | `lifescape agent verify --manifest FILE` | Hashes, versions, and integrity failures |

The existing concise commands remain supported. The new group provides JSON request/response
contracts and command parity for the browser path; it does not replace `lifescape run`.

### Contract rules

- Every command supports `--format json`; JSON is the only agent integration format. Human table
  output is optional and must never be parsed by the agent.
- Each response has `contract_version`, `command`, `status`, `result`, `warnings`, and
  `artifacts`. Errors have a stable `code`, a safe message, field paths where applicable, and a
  documented exit code. Keep structured lifecycle events on stderr as JSON Lines.
- Every file-writing command requires an explicit `--output-dir`, `--scenario`, or `--packet`
  path. It reports the resolved path, content hash, schema version, catalog version, and run ID.
- Mutating commands require an explicit `--confirm` flag. In non-interactive use, absence of the
  flag fails with a typed confirmation error. Read-only commands never mutate local state.
- The command validates input against checked-in JSON Schemas. Publish schemas, sample inputs,
  and an OpenAPI-equivalent command inventory inside the installed wheel.
- The agent can only cite fields in returned structured data. Generated prose is a presentation
  layer over those fields and must satisfy the same FR9 test as browser explanations.
- No command accepts a shell fragment, arbitrary URL fetch, file glob, or model-provided path.
  Inputs are typed values or operator-selected local paths.

## Authority and evidence controls

The following controls are release blockers for every agent command:

1. The discovery result type is distinct from `ObservationRecord`. It cannot be passed to
   `execute_run`, evidence import, gate evaluation, or scoring.
2. A provider response is always Tier C. Provider unavailability returns a visible
   `PROVIDER_UNAVAILABLE` result; it never falls back to invented leads or stale evidence.
3. Fetch results remain review-pending. Only a named human can approve or reject a complete,
   source-policy-valid observation. The review record includes packet ID, reviewer, timestamp,
   source identity, metric, geography, and evidence checksum.
4. Unknown critical evidence blocks a run. Connector failure, source-policy failure, or missing
   provenance produces a named missing state, not a zero, proxy, or substitute geography.
5. The agent receives no secret by default. Provider credentials remain environment-only and are
   not included in output, packets, reports, logs, or command errors.
6. All profile, scenario, packet, evidence, and report files stay local. Remote profiles, shared
   workspaces, automatic publishing, and unattended paid-provider calls require separate product
   and security decisions.

## Delivery sequence

### Phase 0: Make the boundary executable

Before adding AI capabilities, complete the approved discovery spine (current task 2.0): a
versioned official-data catalog, `PlaceDiscoveryService`, typed `SearchProfile` and
`DiscoveryResult`, deterministic normalisation/tie-breaking, and catalog integrity checks.

Deliver the service directly to both FastAPI and Typer. Do not make browser automation the agent
integration strategy. Verify AC2 and AC3, including the proof that discovery never calls
`execute_run`.

### Phase 1: CLI parity for discovery and local scenarios

Implement task 2.0 through 4.0 with command contracts first, then a browser adapter. Add:

- JSON Schemas and a command capability document;
- read-only discovery validation, lookup, and recommendation commands;
- a local, versioned scenario store with explicit decisions, backup/export/reset, catalog and
  algorithm versions; and
- a CLI-to-browser parity test suite that compares canonical JSON results from identical profiles.

The command path must be fully usable without Node, a browser, a local HTTP server, or a provider.
Node/Playwright remain required to test the browser presentation path.

### Phase 2: Evidence handoff and agent-safe research

Complete task 5.0 by exposing evidence readiness and the existing packet lifecycle through the
same typed command contract. Add explicit capabilities for:

- prerequisite matrix generation for selected towns;
- research packet create/select/inspect with a visible Tier C label;
- connector snapshot recording as review-pending only;
- human approve/reject commands that require reviewer identity and an explicit confirmation; and
- approved CSV export followed by the existing strict `lifescape run` authority.

Do not add automatic approval, automatic evidence export, or agent-triggered live provider calls
in this phase. A user can run fetch after reviewing the provider, source, cost, and local output
location.

### Phase 3: Optional conversational adapter

After the deterministic CLI is stable, add a local adapter for a model or desktop agent. Its only
tool surface is `lifescape agent capabilities` and the JSON commands above. It must first show a
draft profile, then obtain confirmation before a state mutation. It must return direct structured
results, not scrape the browser or parse Markdown reports.

Start with fixture-backed end-to-end tests. Add live provider proof only after Brett approves the
provider, privacy statement, rate/cost limits, failure behavior, and real-user validation scope.

### Phase 4: Product polish and measured validation

Complete task 6.0. Align the browser, README, local-app specification, API inventory, and CLI
reference around one statement: *the agent helps operate the local workflow; deterministic
discovery and reviewed evidence make the product result.* Then run the five-household pilot defined
by the PRD. Measure whether households can create useful shortlists and understand the difference
between a lead and verified evidence before expanding model features.

## Acceptance evidence

Do not call the product agent-first or CLI-complete until all of these are true:

- A fresh Python 3.12 environment installs the wheel and completes a fixture-only journey from
  `agent capabilities` to discovery, three local shortlist decisions, evidence-readiness output,
  an admissible reviewed CSV run, reports, and SQLite provenance without starting the web app.
- The same profile produces byte-identical discovery JSON through the domain service, CLI, and
  local API. Catalog or contract changes produce explicit version changes.
- Contract tests cover malformed JSON, unsupported contract version, invalid enum, missing file,
  unsafe output location, lack of `--confirm`, source-policy rejection, geography mismatch,
  missing critical evidence, and provider failure.
- Boundary tests prove a discovery result, research packet, provider text, and fetch snapshot
  cannot enter gate/scoring inputs. A human-approved, source-valid export is the only packet path
  to `execute_run`.
- `pytest`, Ruff, mypy, package tests, benchmark, browser acceptance tests, and security checks
  pass on the exact delivered head. The existing PRD AC1-AC9 remain required.
- The README includes one copy-paste local CLI journey, sample files labelled synthetic, the
  confirmation model, and a statement that no output is a relocation or purchase recommendation.

## Explicit deferrals

- AI-generated recommendations in the default discovery path.
- Autonomous web browsing, scraping, evidence promotion, or evidence approval.
- Agent-controlled paid connectors, routing, purchases, messaging, publishing, or deployment.
- Hosted accounts, shared workspaces, remote scenario storage, or multi-agent delegation.
- A general natural-language shell interface. The supported interface is typed commands and JSON
  Schemas, with a separate optional conversational adapter.

## First implementation slice

Start with BUI-334 task 2.0, but make its public service callable from Typer at the same time:

1. Implement the catalog builder and deterministic `PlaceDiscoveryService`.
2. Define and package `SearchProfile` and `DiscoveryResult` JSON Schemas.
3. Ship `lifescape agent discovery validate|lookup|recommend --format json`.
4. Add fixture parity and boundary tests, then the thin FastAPI routes.
5. Only after that, implement scenario persistence and the browser discovery screen.

This delivers a usable agent integration without changing evidence authority, while advancing the
approved product entrance instead of creating a separate AI product.
