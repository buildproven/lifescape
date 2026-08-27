# PRD: Lifescape Place Discovery

> Status: APPROVED | Author: Brett Stark | Date: 2026-08-26
> Estimate: L | Risk: high

## 1. Overview

Lifescape helps a U.S. household find places where they might want to live. A person can start
with one or two towns they already like, qualities they want, hard requirements, or any
combination of these inputs. Lifescape returns explainable town recommendations, helps the
person refine a shortlist, and then uses verified evidence to test whether the finalists meet the
household's requirements.

Place discovery is the product's front door. The existing evidence engine is the trust layer
behind the shortlist. CSV import, connector operation, and evidence administration are advanced
capabilities, not the primary user journey.

The governing rule remains: **preferences and examples discover; gates eliminate; weights rank;
evidence decides; uncertainty stays visible.**

### Primary persona

Alex is the household member leading a U.S. retirement or long-term relocation search. Alex has
not selected a destination. Alex might know one or two towns that feel promising, but does not
know every comparable town, does not have a research CSV, and cannot supply normalized metric
values. Alex needs to find overlooked options, understand why each option appeared, discuss the
trade-offs with another household member, and decide which towns deserve deeper investigation.

### Historical alignment

This PRD corrects a gap between the original implementation milestone and the intended product:

- The earliest checked-in plan, `docs/implementation-plan.md` at commit `a669a15`, called itself
  only a "Core Vertical Slice" and explicitly deferred automated candidate discovery. It defined
  the decision engine, not the complete user product.
- The original external *Retirement Decision Engine v6* source is recorded in
  `docs/master-spec.md`, but the source file is absent and its complete contents are therefore
  `[unverified]` in the current workspace.
- BUI-334 later defined the missing product entrance: preferences, hard constraints, exclusions,
  zero to two exemplar towns, and 8–15 advisory U.S. town leads without requiring user-supplied
  metric values or a CSV.
- `docs/decisions/ADR-ai-research-packet-boundary.md` states that the product must turn user
  preferences and exemplar towns into candidate leads while keeping discovery material outside
  decision evidence.
- `docs/decisions/ADR-v1-product-boundary.md` made reviewed CSV import the primary v1 journey.
  That decision solved evidence-acquisition scope growth by removing the product's intended front
  door. Approval of this PRD supersedes that product boundary while preserving its evidence
  safeguards.

## 2. Goals

- G1: A first-time user can go from no shortlist to 10 explainable U.S. town recommendations when
  at least 10 recommendable catalog places remain after known hard-constraint and minimum-data
  checks, by supplying one or two liked towns, supported desired qualities, hard constraints, or
  a combination.
- G2: A user can refine the recommendations and save at least three towns as a shortlist without
  preparing files or entering metric values.
- G3: Every recommendation shows the criteria that caused it to appear, important trade-offs,
  missing discovery data, and the distinction between discovery information and verified
  decision evidence.
- G4: A user can promote two or more shortlisted towns into the existing evidence-backed
  comparison flow without re-entering the search profile.
- G5: Tier C, synthetic, missing, or otherwise unverified discovery material cannot clear a gate,
  affect an evidence-backed score, or appear as verified evidence.

## 3. Non-goals

- Hosted accounts, shared household collaboration, payments, or remote profile storage.
- Real-estate listings, property purchase recommendations, or automated neighborhood selection.
- AI-generated town facts or unconstrained model-generated recommendation lists in the default
  discovery path.
- Complete live verification of every recommended town before the user creates a shortlist.
- Hiding unknown values, inferring evidence, or treating a discovery match as proof that a hard
  requirement is satisfied.
- Building a new connector, routing service, scraper, or provider integration unless an approved
  requirement in this PRD needs it for the next user-visible vertical slice.
- Replacing the existing deterministic gate, scoring, sensitivity, provenance, or reporting
  engine.

## 4. User stories

- As Alex, I want to enter towns I already like so that Lifescape can find places with similar
  characteristics that I may not know.
- As Alex, I want to describe the life I want and my hard limits so that recommendations reflect
  more than geographic similarity.
- As Alex, I want to see why each town was recommended and where it differs from my examples so
  that I can judge the trade-offs.
- As Alex, I want to reject, keep, and refine recommendations so that the search improves without
  restarting.
- As Alex, I want unknown and weak data to remain visible so that I do not mistake an interesting
  lead for a safe decision.
- As Alex, I want to move shortlisted towns into a stricter comparison so that evidence can test
  the candidates before I plan visits or make a relocation decision.

## 5. Functional requirements

- FR1: The primary local-app action is **Find places**, not CSV import. The first screen explains
  that Lifescape finds candidate towns and then verifies finalists.
- FR2: A search profile accepts zero to two normalized U.S. exemplar towns, desired qualities,
  hard constraints, exclusions, and relative priority weights. The user must supply at least one
  exemplar town or two supported desired qualities. An exemplar supplies targets for every
  supported dimension that is non-null for that exemplar.
- FR3: Desired qualities and hard constraints are separate inputs. A quality changes discovery
  relevance. A hard constraint excludes a town only when the discovery dataset contains a value
  that proves failure; an unknown value remains visible and does not count as a pass.
- FR4: The first catalog supports town size, housing cost, population density, car-light commute
  share, college-educated share, older-adult share, and preferred or excluded states or regions.
  **Car-light commute share is an ACS commute-mode proxy, not a walkability score.** Each supported
  dimension has a documented field definition, unit, observation date, source or derivation, and
  a non-null value for at least 80% of catalog places with population of 2,500 or more. Climate,
  healthcare access, nature and outdoor access, airport access, and cultural or social activity
  remain named discovery needs, but the first catalog does not accept them as scored qualities or
  claim matches for them. Adding one requires a PRD amendment with a source and shipped-catalog
  coverage criterion.
- FR5: Discovery uses a versioned catalog of U.S. incorporated places and Census-designated
  places. The catalog contains normalized place identity and the available discovery dimensions.
  Missing fields remain null.
- FR6: For the same catalog version and search profile, candidate generation is deterministic.
  It uses a documented similarity calculation over normalized discovery fields and applies hard
  exclusions before ordering candidates.
- FR7: A successful initial search returns 10 distinct recommendations when at least 10 catalog
  places satisfy the known hard constraints and the FR8a minimum-data rule. It never returns either
  exemplar town as a new recommendation. If fewer than 10 places are recommendable, it returns all
  recommendable places and states the number excluded by each hard constraint and by insufficient
  match data.
- FR8: Each recommendation includes its place and state, total discovery-match score, component
  match contributions, two or more concrete match reasons, important differences
  from the search profile or exemplars, missing discovery fields, catalog version, and data date.
- FR8a: A candidate requires at least two non-null, non-region match components. A candidate with
  fewer than two components is not recommended and is counted in insufficient-data diagnostics.
  A match component is one supported discovery dimension for which the profile has a target and
  the candidate has a non-null catalog value; it records the target, candidate value, normalized
  similarity, weight, and weighted contribution. State or region inclusion is a filter, not a
  match component.
- FR9: Generated prose can summarize structured match data, but it cannot create a match reason,
  fact, value, or constraint result that is absent from the structured discovery result.
- FR10: The user can mark a recommendation **Keep**, **Not for me**, or **Unsure**. The user can
  change priorities or constraints and rerun the search. The app preserves these decisions in the
  local scenario and shows whether a recommendation moved because an input changed.
- FR11: The user can open a **Why this place?** view that compares the candidate with each exemplar
  and the user's criteria. The view labels every field as discovery data, missing, or verified
  evidence; it does not use a single unexplained score as the rationale.
- FR12: The user can save at least three recommendations to a shortlist and add a town manually.
  A shortlist records the search profile, catalog version, recommendation details, and user
  decisions in local storage controlled by the application. It also records the discovery
  algorithm version and normalization version returned by the search.
- FR13: The user can promote any two or more shortlisted towns into evidence review. The evidence
  flow reuses the search profile where its fields map to existing gates or weights, displays every
  required metric and its evidence state, and invokes `execute_run` only with admissible evidence.
- FR14: A discovery recommendation cannot itself satisfy an evidence requirement. Unknown
  critical evidence continues to block a finalist. Tier C and synthetic material cannot affect
  gates or evidence-backed scores.
- FR15: Reviewed CSV import remains available under an **Advanced evidence import** action for an
  expert who already has a shortlist. It is not required to discover, refine, or save candidate
  towns.
- FR16: The read-only hosted example uses static, clearly synthetic content to illustrate the
  discovery-to-shortlist-to-verification sequence. It does not execute discovery, accept personal
  input, or add hosted API routes until a separate hosted-product PRD is approved.

## 6. Non-functional requirements

- Performance: Catalog integrity verification and loading complete in at most 3 seconds, and a
  subsequent search across the supported U.S. place catalog returns in at most 2 seconds at the
  95th percentile on the repository's CI runner.
- Reproducibility: The search profile, catalog version, normalization configuration, and algorithm
  version are sufficient to reproduce the ordered result and component scores byte for byte.
- Privacy: Search profiles and user decisions remain local. No profile content leaves the device
  unless the user explicitly enables a provider in a later approved product requirement.
- Local-state compatibility: The browser state uses integer `schema_version: 1`. Additive optional
  fields do not change that version. A breaking shape change requires a tested migration to the
  next integer version. Without a migration, the app preserves the original value under a backup
  key, offers JSON export and reset, and does not partly load it. A matching-schema shortlist from
  an older catalog remains readable with its original recommendations and catalog label; the app
  offers a rerun but does not silently rescore or reset it.
- Local-state corruption: JSON parse failure, missing required fields, invalid enum values, or any
  other schema-validation failure follows the same backup, JSON export, and explicit reset path as
  an unsupported schema version. The app never partly loads a malformed state.
- Security: Search text is treated as untrusted input. It cannot select file paths, execute code,
  or inject markup into the result page.
- Accessibility: The complete search, refinement, shortlist, and handoff journey meets WCAG 2.1
  AA keyboard, label, focus, contrast, and status-announcement requirements.
- Browser support: The journey works in the Playwright-managed Chromium desktop and mobile
  viewports used by this repository.
- Compatibility: The application remains local-first and supports Python 3.12 and the repository's
  declared Node.js version.
- Evidence integrity: Existing strict configuration, source policy, missing-data, geography,
  provenance, gate, scoring, sensitivity, and synthetic-data invariants remain enforced.

## 7. Design considerations

The interface should reveal complexity in stages:

1. **Tell us what feels right** — exemplar towns and desired qualities.
2. **Set your boundaries** — hard constraints and exclusions.
3. **Explore matches** — explainable recommendations and trade-offs.
4. **Shape a shortlist** — keep, reject, refine, or add a town.
5. **Verify finalists** — evidence completeness, gates, ranking, sensitivity, and reports.

The result card must prioritize the place name, the reasons it matched, and the largest trade-off.
Provenance and component details remain available without making the initial result a data-entry
or evidence-administration screen. Use the existing visual system unless a reviewed design change
is necessary for this journey.

## 8. Success metrics

- Primary: At least four of the first five representative U.S. relocation-planning households can
  save three or more genuinely interesting towns within 10 minutes of starting an unguided local
  session.
- Activation: In the same pilot, at least four of five households reach a recommendation list
  without preparing a CSV or receiving operator assistance.
- Explanation: Each pilot household can identify at least one displayed reason and one displayed
  trade-off for every town it saves.
- Guardrail: Automated boundary tests demonstrate zero paths from discovery records into a gate or
  evidence-backed score without promotion through the existing admissible-evidence contract.
- Guardrail: Synthetic or missing data remains visibly labeled in every browser fixture and
  exported artifact that contains it.

The primary, activation, and explanation metrics are external pilot-validation gates, not
deterministic source-release gates. A dated pilot record must identify five distinct household
sessions, each session's start and shortlist timestamps, shortlist size, and answers to the
reason/trade-off checks. Catalog-field expansion remains separately machine-gated by FR4 and AC2.

## 9. Risks

- Risk: Recommendations feel generic because the catalog has too few useful dimensions. →
  Mitigation: Measure pilot shortlist creation before adding infrastructure; add a dimension only
  when pilot evidence shows a repeated missing distinction.
- Risk: A similarity score creates false confidence. → Mitigation: Show component contributions,
  trade-offs, missing fields, and data dates; reserve the evidence-backed ranking for finalists.
- Risk: Liked towns encode accidental qualities the user does not want. → Mitigation: Ask what the
  user likes about each exemplar and let explicit priorities override exemplar similarity.
- Risk: Hard constraints remove good candidates because discovery data is incomplete. →
  Mitigation: Unknown never counts as failure or pass during discovery; show it as **Needs
  verification**.
- Risk: Provider and evidence work again displaces the user journey. → Mitigation: Apply the scope
  controls below and accept infrastructure only when it unlocks an approved vertical slice.
- Risk: A model invents a rationale. → Mitigation: Generate every reason from typed component data
  and test that prose contains no unsupported claims.
- Risk: A damaged or partial packaged catalog produces confident recommendations from an
  incomplete universe. → Mitigation: Verify the manifest output hash and row count before exposing
  discovery. A mismatch returns a visible `CATALOG_UNAVAILABLE` failure and no recommendations;
  it never loads partial rows.

## 10. Open questions

- None for the product boundary. Dataset selection and licensing are implementation-design tasks;
  every selected source must satisfy FR4–FR6 and the repository source policy before its data is
  shipped.

## 11. Scope control and work traceability

This file is the canonical product source of truth after Brett approves it. It controls what
Lifescape does; architecture decisions control how approved requirements are implemented.

- Every Lifescape Linear issue must cite this PRD and list the specific goal, functional
  requirement, or non-functional requirement it advances.
- Every product PR must include a **PRD trace** with the same requirement identifiers and must name
  the user-visible behavior delivered by the revision.
- Work that cannot cite an approved requirement is out of scope. It requires a PRD amendment and
  Brett's approval before implementation.
- Infrastructure, connectors, data acquisition, provider provisioning, and evidence operations
  cannot be roadmap goals by themselves. They must be the smallest dependency of an approved
  user-visible vertical slice.
- A parent task is complete only when a user can observe and verify its vertical behavior. A
  schema-only, connector-only, API-only, or UI-only milestone is not a finished product slice.
- ADRs may refine architecture or evidence boundaries. They cannot redefine the product entrance,
  target user, goals, success metrics, or non-goals without amending this PRD.
- The discovery-to-shortlist browser journey is the release acceptance spine. Quality gates must
  protect that journey in addition to lower-level engine behavior.
- Backlog priority follows the user journey: discovery input, useful recommendations,
  explanation/refinement, shortlist, then evidence-backed finalist verification. Provider depth
  comes only when the next journey stage requires it.

## Acceptance criteria (must be machine-verifiable)

- [ ] AC1: `uv run --extra dev pytest tests/test_product_contract.py` confirms that the README,
  implementation plan, PR template, and active product-boundary ADR reference this PRD and its
  traceability rule.
- [ ] AC2: `uv run --extra dev pytest tests/test_discovery.py` confirms that a fixed catalog and
  profile produce the specified ordered recommendations, at least two non-region reasons per
  candidate, component contributions, hard-constraint exclusions, null handling, exemplar
  exclusion, catalog-integrity failure, and byte-identical repeat output. The same test confirms
  at least 80% non-null coverage for every supported field among shipped catalog places with
  population of 2,500 or more.
- [ ] AC3: `uv run --extra dev pytest tests/test_web.py -k discovery` confirms that
  `GET /api/places` validates lookup input and that `POST /api/place-recommendations` returns the
  documented success body or existing FastAPI `detail` error body without server persistence or
  an `execute_run` call.
- [ ] AC4: `uv run --extra dev pytest tests/test_user_journey.py -k discovery` confirms at 390 px
  and 1440 px
  that a user can choose an exemplar, set criteria, receive recommendations, inspect reasons and
  unknowns, keep three towns, reload the page, and recover the same shortlist.
- [ ] AC5: `uv run --extra dev pytest tests/test_user_journey.py -k evidence_handoff` confirms that
  two kept towns reach evidence review, every critical metric is visible as verified or missing,
  and a discovery record alone cannot enable the comparison action.
- [ ] AC6: `uv run --extra dev pytest tests/test_source_policy.py tests/test_missing_data.py
  tests/test_discovery.py` confirms that Tier C, synthetic, and missing discovery fields cannot
  satisfy a gate or affect an evidence-backed score.
- [ ] AC7: `uv run lifescape benchmark --output-dir outputs/benchmark` produces 5 eligible and 5
  blocked synthetic towns and retains visible synthetic warnings.
- [ ] AC8: `npm run quality:check` passes with zero Ruff, mypy, frontend lint, formatting, test,
  coverage, browser, or package-build failures.
- [ ] AC9: `npm run security:check` reports zero production dependency vulnerabilities and zero
  detected secrets in the required repository scans.
