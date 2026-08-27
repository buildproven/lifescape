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
Gazetteer and American Community Survey bulk files. Its first supported dimensions are population,
housing cost, population density, car-light commute share, college-educated share, older-adult
share, and state or region. It does not claim climate, healthcare, nature, airport, culture, or
walkability matches. A checked-in build script and manifest record source URLs, source vintage,
hashes, selected fields, derivations, output hash, row count, and per-field coverage. A shipped
field is supported only when at least 80% of catalog places with population of 2,500 or more have
a non-null value. Discovery fields remain discovery data. They do not become `ObservationRecord`
values and cannot enter `execute_run`.

The local browser stores only a versioned search profile, recommendation dispositions, and
shortlist identity. It does not store evidence claims. The state uses integer `schema_version: 1`.
Additive optional fields retain the version. A breaking change must increment the integer and ship
a tested migration. Without a migration, the app preserves the original value under a backup key,
offers JSON export and reset, and does not partly load it. A matching-schema shortlist from an
older catalog stays readable with its original recommendations and catalog label; the app offers a
rerun but does not silently rescore or reset it.

Expose two resources:

- `GET /api/places?query=<text>&limit=<n>` for normalized exemplar/manual-town lookup.
- `POST /api/place-recommendations` as a stateless calculation. It returns HTTP 200 with
  `{ "data": { "profile": ..., "recommendations": ..., "diagnostics": ... } }`, creates no
  server record, returns no resource ID or `Location` header, and has no corresponding `GET`.

New discovery endpoints use `{ "data": ... }` for success and
`{ "error": { "code": string, "message": string, "details"?: object } }` for failure. Lookup and
recommendation success return 200. Invalid syntax returns 400. Valid syntax with invalid semantics
returns 422. A catalog hash, row-count, parse, or load failure returns 503 with code
`CATALOG_UNAVAILABLE`. Fewer than 10 qualifying recommendations is a 200 success with exclusion
and insufficient-data counts in `diagnostics`. Existing API endpoints retain their historical bare
response shapes in this revision; this intentional inconsistency prevents an unrelated migration.

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
- The packaged catalog hash and row count are verified before any row is searchable. Integrity
  failure disables discovery and returns `CATALOG_UNAVAILABLE`; partial catalogs are never used.

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

Claude independently reviewed commit `b25a7dd` before implementation and reported six blocking
and five non-blocking findings. This revision resolves them by limiting first-release dimensions
to populated Census fields with an 80% coverage gate; defining stateless API and envelope
semantics; defining local-state compatibility, backup, export, and stale-catalog behavior; failing
closed on catalog integrity errors; making the hosted example explicitly static; conditioning the
10-result goal; aligning clean-checkout test commands; requiring two match components; and adding
a catalog-load budget. A second independent review is required before implementation starts.
