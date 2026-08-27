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

The full catalog is available for lookup and manual addition. The generated-candidate serving
universe is limited to places with population of 2,500 or more. Catalog coverage and normalization
bounds use that same serving universe. Smaller exemplar towns may supply their non-null values as
targets but are not generated as recommendations.

Normalization version `discovery-winsorized-minmax-v1` stores each supported field's 5th and 95th
percentile bounds over the serving universe in the catalog manifest. Values are clipped to those
bounds and mapped linearly to 0–1. Candidate-to-target similarity is
`1 - abs(normalized_candidate - normalized_target)`. Changing fields, percentiles, serving
population, clipping, mapping, or similarity requires a new normalization version.

The local browser stores a versioned search profile, the complete structured recommendation result
snapshot, recommendation dispositions, and shortlist identity. The snapshot includes catalog,
algorithm, and normalization versions so it remains explainable without silently recalculating.
It does not store evidence claims. The state uses integer `schema_version: 1`.
Additive optional fields retain the version. A breaking change must increment the integer and ship
a tested migration. Without a migration, the app preserves the original value under a backup key,
offers JSON export and reset, and does not partly load it. A matching-schema shortlist from an
older catalog stays readable with its original recommendations and catalog label; the app offers a
rerun but does not silently rescore or reset it.
JSON parse failure, missing required fields, invalid enums, or any other schema-validation failure
uses the same backup, JSON export, and explicit reset path. The raw value is never partly loaded.
A stale snapshot embeds each component's label, definition, values, and source catalog version. If
a current catalog no longer supports that field, **Why this place?** labels it as historical
discovery data and does not rescore or apply current coverage rules to it.

Expose two resources:

- `GET /api/places?query=<text>&limit=<n>` for normalized exemplar/manual-town lookup. `query`
  contains 2–120 characters. `limit` defaults to 10 and accepts 1–20.
- `POST /api/place-recommendations` as a stateless calculation. It returns HTTP 200 with
  `{ "profile": ..., "catalog_version": ..., "algorithm_version": ...,
  "normalization_version": ..., "recommendations": ..., "diagnostics": ... }`, creates no server
  record, returns no resource ID or `Location` header, and has no corresponding `GET`.

Both routes explicitly return 404 when `hosted_demo=True`, in addition to the static-boundary
middleware. The discovery service verifies and loads the catalog once during local `create_app`
construction. Stateless means the POST retains no per-user server state; it does not mean the
catalog is reloaded per request.

`POST /api/place-recommendations` calls the existing `_validate_mutation_origin` guard with
`require_origin=True` before reading the profile or calculating results. A missing or foreign
origin receives the existing 403 `detail` response. Stateless calculation does not exempt a local
endpoint from the local-origin privacy boundary. `BodyLimitMiddleware` is extended to reject this
route above 65,536 bytes before JSON parsing, with the existing 413 `detail` shape.

The response `diagnostics` object has this stable shape:

```json
{
  "catalog_places": 32000,
  "serving_places": 21000,
  "exemplar_count": 1,
  "known_constraint_exclusions": [
    {"constraint_id": "population_max", "excluded_count": 1200, "unknown_count": 34}
  ],
  "excluded_any_constraint_count": 1200,
  "insufficient_match_data_count": 41,
  "recommendable_count": 19758,
  "returned_count": 10
}
```

`known_constraint_exclusions` contains one entry for every submitted hard constraint, including
zero counts, ordered by `constraint_id`. `excluded_count` counts known failures. `unknown_count`
counts candidates whose null value neither passes nor fails that constraint. The other counts are
nonnegative integers. Per-constraint exclusion sets may overlap and their counts are not additive.
`excluded_any_constraint_count` is the union count. `insufficient_match_data_count` is evaluated
only after constraint exclusion. This identity must hold:

```text
serving_places - exemplar_count - excluded_any_constraint_count
- insufficient_match_data_count = recommendable_count
```

`returned_count` is `min(10, recommendable_count)`.

New discovery endpoints follow the repository's existing FastAPI response convention: a bare JSON
success body and `{ "detail": string | list }` for errors. Lookup and recommendation success return
200. Invalid syntax or query bounds return FastAPI's existing 422 validation response. A catalog
hash, row-count, parse, or load failure returns 503 with `detail` equal to a safe
`CATALOG_UNAVAILABLE: ...` message. Origin, body-size, rate-limit, and hosted-boundary middleware
retain their current `detail` responses. Fewer than 10 qualifying recommendations is a 200 success
with exclusion and insufficient-data counts in `diagnostics`. A repository-wide response-envelope
migration is outside this PRD and must not be introduced in this slice.

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

- The same profile, catalog version, algorithm version, and normalization version produce
  byte-identical ordered results.
- Unknown discovery data does not count as either a constraint pass or a similarity match.
- A known failed hard constraint excludes a candidate and records the reason.
- An exemplar cannot be returned as its own recommendation.
- Every score contribution refers to a typed catalog field and includes the compared values.
- A match component is a supported non-region dimension with a profile target and non-null
  candidate value. It includes target, candidate value, normalized similarity, weight, and weighted
  contribution. A recommendable candidate has at least two match components.
- An explicit target overrides exemplar targets for its dimension. Otherwise, a dimension uses the
  highest similarity to either exemplar, contributes once, and records every available target plus
  the matched exemplar target.
- A validated profile has at least two supported non-region targets after exemplar resolution.
- Recommendations order by total weighted match descending, then canonical `place_id` ascending.
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
- API tests cover lookup, validation, success and `detail` error bodies, deterministic repeat
  output, and
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
a catalog-load budget. The second review of commit `6a8452d` confirmed the evidence boundary,
catalog coverage, stateless lifecycle, corruption behavior, and hosted boundary, but found six
remaining contract contradictions. This revision aligns new errors with the existing FastAPI
shape; defines recommendable places and match components; requires an exemplar or two qualities;
stores a complete versioned result snapshot; defines malformed-state recovery; marks the prior
product ADR superseded; and adds algorithm, normalization, and lookup-bound contracts. A third
independent review is required before implementation starts.
