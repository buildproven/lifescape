# ADR: Use a versioned local catalog behind one deterministic discovery contract

## Status

Accepted for BUI-334 implementation, subject to independent architecture review.

## PRD trace

`docs/prd/lifescape-place-discovery.md`: G1–G5, FR2–FR14, Reproducibility, Privacy,
and Evidence integrity.

## Decision

Add one deep discovery module with this conceptual interface:

```text
PlaceDiscoveryService.search(SearchProfile) -> DiscoveryResult
```

The module owns profile validation, place identity, hard-constraint evaluation, missing-value
behavior, normalization, exemplar similarity, ordering, component contributions, explanations,
catalog and algorithm versions, and deterministic tie-breaking. The web API and browser use this
interface; they do not calculate recommendation scores.

The default provider is a packaged, versioned discovery catalog built from official U.S. Census
Gazetteer and American Community Survey bulk files. A checked-in build script and manifest record
source URLs, source vintage, hashes, selected fields, derivations, output hash, and row count.
Discovery fields remain discovery data. They do not become `ObservationRecord` values and cannot
enter `execute_run`.

The local browser stores only a versioned search profile, recommendation dispositions, and
shortlist identity. It does not store evidence claims. An incompatible or malformed local record
fails visibly and offers a reset; it is never silently repaired or partly loaded.

Expose two resources:

- `GET /api/places?query=<text>&limit=<n>` for normalized exemplar/manual-town lookup.
- `POST /api/discovery-searches` for an immutable search result with a success `data` envelope or
  error envelope.

The existing `/api/research/*` packet and provider endpoints remain an experimental evidence
research surface. They are not the default discovery provider and are not called by the primary
journey.

## Why this seam is deep

A caller supplies household intent and receives an explainable result. It does not need to know
how catalog fields are normalized, how exemplar distances combine, how nulls affect denominators,
how constraints fail, how ties break, or how explanations are derived. Deleting the module would
spread those policies into the API, browser, and tests.

## Alternatives

- Use the existing Claude discovery provider as the primary path. Rejected because output is not
  deterministic, requires credentials and spend, and can invent town facts or rationales.
- Query Census or other providers during each search. Rejected because network availability,
  upstream changes, latency, and credentials would make local results non-reproducible.
- Put scoring logic in browser JavaScript. Rejected because it would duplicate engine policy,
  expose a second implementation seam, and make package/API callers disagree.
- Reuse `ObservationRecord` for discovery fields. Rejected because it would collapse advisory
  matching data into the evidence authority boundary.

## Invariants

- The same profile, catalog version, and algorithm version produce byte-identical ordered results.
- Unknown discovery data does not count as either a constraint pass or a similarity match.
- A known failed hard constraint excludes a candidate and records the reason.
- An exemplar cannot be returned as its own recommendation.
- Every score contribution refers to a typed catalog field and includes the compared values.
- Generated reasons contain no fact absent from the structured result.
- Discovery data cannot satisfy a gate, change an evidence-backed score, or invoke `execute_run`.
- Synthetic catalog fixtures remain visibly synthetic and cannot ship as the real catalog.
- Search profiles and shortlist decisions remain local unless a later approved PRD changes that
  privacy boundary.

## Migration and rollback

This is an additive domain and API. The current evidence import and engine inputs remain valid.
Rollback removes the primary discovery UI, new endpoints, discovery module, and packaged catalog;
the advanced CSV comparison can remain available without a database migration. Local browser
records use a feature-specific key and schema version, so rollback leaves inert client data that a
later compatible version can ignore or explicitly reset.

## Verification

- Module tests use a small independent fixture with worked expected ordering and contributions.
- API tests cover lookup, validation, success/error envelopes, deterministic repeat output, and
  proof that `execute_run` is not called.
- Browser tests cover search, explanation, shortlist persistence, recovery, and evidence handoff.
- Boundary tests attempt to pass discovery records to evidence and scoring seams and assert
  rejection.
- The catalog build is reproducible from manifest inputs and verifies source and output hashes.

## Review record

Pending independent review before implementation.
