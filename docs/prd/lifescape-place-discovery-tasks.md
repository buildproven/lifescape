# Tasks: Lifescape Place Discovery

> PRD: `docs/prd/lifescape-place-discovery.md`
> Linear: BUI-334

- [x] 0.0 Create feature branch `docs/bui-334-place-discovery-prd`
  - Delivers: approved work is isolated from the primary checkout.
  - Blocked by: none.
  - Verification: `git branch --show-current` prints `docs/bui-334-place-discovery-prd`.
- [x] 1.0 Make the approved PRD the enforceable product contract
  - Delivers: contributors can identify Lifescape's front door and must trace product work to an
    approved requirement.
  - Blocked by: 0.0.
  - Verification: `uv run --extra dev pytest tests/test_product_contract.py`.
  - [x] 1.1 Approve `docs/prd/lifescape-place-discovery.md` and add machine-verifiable criteria.
  - [x] 1.2 Supersede the CSV-only product boundary without weakening evidence invariants.
  - [x] 1.3 Link the PRD from README, implementation guidance, and the PR template.
  - [x] 1.4 Add product-contract tests through the versioned repository surface.
  - [x] 1.5 Run the evidence-backed affected tests; if green, commit.
- [ ] 2.0 Find places from exemplar towns and explicit criteria
  - Delivers: a user can choose zero to two liked towns and criteria and receive deterministic,
    explainable recommendations from a real, versioned U.S. place catalog.
  - Blocked by: 1.0.
  - Verification: `uv run --extra dev pytest tests/test_discovery.py tests/test_web.py -k
    discovery`.
  - [ ] 2.1 Add the reviewed discovery architecture decision and source manifest.
  - [ ] 2.2 Build a reproducible catalog from official Census geography and ACS bulk files.
  - [ ] 2.3 Add a deep discovery module for profiles, constraints, similarity, and explanations.
  - [ ] 2.4 Expose validated place lookup and stateless place-recommendation HTTP resources.
  - [ ] 2.5 Add red-capable module and API behavior tests with independent fixture oracles.
  - [ ] 2.6 Run the evidence-backed affected tests; if green, commit.
- [ ] 3.0 Explore and understand recommendations in the local app
  - Delivers: a user can enter the search journey, see 10 recommendations, and inspect the match
    reasons, differences, unknowns, dates, and source boundary for each town.
  - Blocked by: 2.0.
  - Verification: `uv run --extra dev pytest tests/test_user_journey.py -k discovery` at mobile
    and desktop.
  - [ ] 3.1 Replace CSV import as the primary screen action with the staged discovery profile.
  - [ ] 3.2 Render recommendation order, match contributions, reasons, trade-offs, and unknowns.
  - [ ] 3.3 Add loading, validation, empty, failure, keyboard, focus, and narrow-screen states.
  - [ ] 3.4 Preserve advanced CSV import outside the primary journey.
  - [ ] 3.5 Add browser tests through the user's public journey.
  - [ ] 3.6 Render, review, and revise the interface at 320, 768, 1024, and 1440 px.
  - [ ] 3.7 Run the evidence-backed affected tests; if green, commit.
- [ ] 4.0 Refine recommendations and keep a local shortlist
  - Delivers: a user can keep, reject, or mark towns unsure; change criteria; and recover the same
    versioned shortlist after reloading the local app.
  - Blocked by: 3.0.
  - Verification: the shortlist persistence section of
    `uv run --extra dev pytest tests/test_user_journey.py -k discovery`.
  - [ ] 4.1 Add the versioned local scenario schema, migration rule, backup/export path, and stale
    catalog behavior.
  - [ ] 4.2 Add Keep, Not for me, Unsure, refinement, and movement explanations.
  - [ ] 4.3 Add manual-town entry through the normalized catalog lookup.
  - [ ] 4.4 Test local persistence, reload, reset, and malformed-state recovery.
  - [ ] 4.5 Run the evidence-backed affected tests; if green, commit.
- [ ] 5.0 Hand shortlisted towns to evidence-backed verification
  - Delivers: a user can move two or more kept towns into evidence review, see every missing
    critical metric, and use reviewed CSV import without letting discovery clear a gate.
  - Blocked by: 4.0.
  - Verification: `uv run --extra dev pytest tests/test_user_journey.py -k evidence_handoff` plus
    AC6.
  - [ ] 5.1 Map shortlist identity and relevant profile values into the evidence-review entrance.
  - [ ] 5.2 Show verified, missing, and blocked evidence states before comparison.
  - [ ] 5.3 Keep the run action disabled until admissible evidence exists for at least two towns.
  - [ ] 5.4 Test shortlist handoff and the discovery/evidence authority boundary.
  - [ ] 5.5 Run the evidence-backed affected tests; if green, commit.
- [ ] 6.0 Align explanation, documentation, and release evidence
  - Delivers: the README and hosted synthetic example explain the same discovery-first product,
    and the exact candidate passes the repository's complete delivery workflow.
  - Blocked by: 5.0.
  - Verification: AC1 and AC7–AC9, independent review, exact-head CI, and merged-main CI.
  - [ ] 6.1 Update README, local-app specification, implementation plan, limitations, architecture,
    API inventory, and hosted synthetic copy with PRD traces.
  - [ ] 6.2 Update BUI-334 with requirement, test, and delivery evidence.
  - [ ] 6.3 Run the full quality, security, benchmark, and package matrix.
  - [ ] 6.4 Run independent review, resolve findings, and merge the exact reviewed head.
  - [ ] 6.5 Verify merged-main CI and clean exact repository convergence.
