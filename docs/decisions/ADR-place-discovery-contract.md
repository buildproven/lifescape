# ADR: Use a versioned local catalog behind one deterministic discovery contract

## Status

Accepted and implemented for BUI-334. The "As built" section at the end records refinements made
during implementation; each is additive and consistent with the PRD.

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

Every selected first-release field has a nonnegative valid domain. During the catalog build, any
negative ACS estimate or documented ACS missing/suppression annotation value, including
`-666666666` and `-999999999`, becomes null before derivation, coverage, bounds, or output. The
manifest records the sentinel policy, and catalog tests assert that no negative value remains.

The full catalog is available for lookup and manual addition. The exemplar and generated-candidate
serving universe is limited to places with known population of 2,500 or more. A null population or
population below 2,500 excludes a place from that universe. Catalog coverage and normalization
bounds use that same serving universe. Smaller or unknown-population towns can be manually added
to a shortlist but cannot be exemplars or generated recommendations in this release. Every lookup
item includes boolean `serving_eligible`. The browser disables exemplar selection and explains the
population boundary when it is false, but still permits manual shortlist addition. A recommendation
request naming a non-eligible exemplar returns 422 before profile resolution.

Normalization version `discovery-winsorized-minmax-v1` stores each supported field's 5th and 95th
percentile bounds over the serving universe in the catalog manifest. Values are clipped to those
bounds and mapped linearly to 0–1. Candidate-to-target similarity is
`1 - abs(normalized_candidate - normalized_target)`. Changing fields, percentiles, serving
population, clipping, mapping, or similarity requires a new normalization version.
Percentiles use the nearest-rank rule: sort non-null values ascending and select
`ceil(percentile * count) - 1`, bounded to the first and last index.
Every component retains the raw target and candidate values, normalized values, lower and upper
bounds, and `target_clipped` and `candidate_clipped` booleans. The explanation view displays a
clipping notice and never calls clipped equality an exact raw-value match.
Two raw values clipped to the same catalog edge therefore receive similarity 1.0 by design; the
structured component and explanation disclose both raw values and the clipping caveat.

Profile priorities are integers 1–5 and default to 3 per target. A candidate total is
`sum(weight * similarity for present components) / sum(weight for all profile targets)`. A missing
candidate field contributes no numerator while its weight stays in the denominator. This prevents
missing fields from increasing a score. The total is in 0–1 and is reported as a rounded 0–100
display percentage only at the presentation boundary.
Recommendations order by total descending, then present match-component count descending, then
canonical `place_id` ascending. This preserves deterministic ordering while preferring the result
supported by more profile dimensions when totals tie.

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
  contains 2–120 characters. `limit` defaults to 10 and accepts 1–20. Each item contains canonical
  place identity, display name, state, population when known, `serving_eligible`, and the
  catalog `values` for every supported field (null when missing) so the browser can show the
  resolved target count before submit (PRD FR2b).
- `POST /api/place-recommendations` as a stateless calculation. It returns HTTP 200 with
  `{ "profile": ..., "catalog_version": ..., "algorithm_version": ...,
  "normalization_version": ..., "recommendations": ..., "diagnostics": ... }`, creates no server
  record, returns no resource ID or `Location` header, and has no corresponding `GET`.

Both routes explicitly return 404 when `hosted_demo=True`, in addition to the static-boundary
middleware. The discovery service verifies and loads the catalog once during local `create_app`
construction. A load or integrity error is retained as a degraded-service state rather than raised
from `create_app`; lookup and recommendation routes convert that state to the specified 503 while
the local page and advanced evidence flow remain available. Stateless means the POST retains no
per-user server state; it does not mean the catalog is reloaded per request.
Catalog verification and loading have no runtime deadline because partial verification is unsafe.
The app records elapsed load time. A release benchmark fails when it exceeds three seconds on the
repository CI runner, but elapsed time alone never creates degraded-service state.

`POST /api/place-recommendations` calls the existing `_validate_mutation_origin` guard with
`require_origin=True` before reading the profile or calculating results. A missing or foreign
origin receives the existing 403 `detail` response. Stateless calculation does not exempt a local
endpoint from the local-origin privacy boundary. `BodyLimitMiddleware` is extended to reject this
route above 65,536 bytes before JSON parsing. The middleware accepts per-path limits and messages;
this route returns 413 with `detail` equal to “recommendation request exceeds the 64 KB limit,”
while evidence import retains its existing 5 MB message.

The response `diagnostics` object has this stable shape:

```json
{
  "catalog_places": 32000,
  "serving_places": 21000,
  "serving_exemplar_count": 1,
  "user_excluded_count": 0,
  "missing_population_count": 6000,
  "below_minimum_population_count": 5000,
  "known_constraint_exclusions": [
    {"constraint_id": "population_max", "excluded_count": 1200, "unknown_count": 34}
  ],
  "excluded_any_constraint_count": 1200,
  "insufficient_match_data_count": 41,
  "recommendable_with_unknown_constraints_count": 34,
  "recommendable_count": 19758,
  "returned_count": 10
}
```

`known_constraint_exclusions` contains one entry for every submitted hard constraint, including
zero counts, ordered by `constraint_id`. `excluded_count` counts known failures. `unknown_count`
counts candidates whose null value neither passes nor fails that constraint. The other counts are
nonnegative integers. Per-constraint exclusion sets may overlap and their counts are not additive.
`serving_exemplar_count` counts only exemplars inside the serving universe. Serving exemplars and
towns the user marked **Not for me** (`exclude_places`, counted in `user_excluded_count`) are
removed before constraint evaluation and cannot appear in a later exclusion count.
`excluded_any_constraint_count` is the union count after exemplar removal.
`insufficient_match_data_count` is evaluated only after constraint exclusion. These sets are
disjoint and this identity must hold:

```text
serving_places - serving_exemplar_count - user_excluded_count
- excluded_any_constraint_count - insufficient_match_data_count = recommendable_count
```

`returned_count` is `min(10, recommendable_count)`.
Unknown constraint values do not remove a candidate. Every affected recommendation lists its
`unknown_constraints`; `recommendable_with_unknown_constraints_count` counts the union of such
recommendable candidates and is less than or equal to `recommendable_count`. Per-constraint
`unknown_count` values can overlap and are not additive. `missing_population_count` reports full
catalog places with null population. `below_minimum_population_count` reports places with known
population below 2,500. The catalog partition must satisfy:

```text
catalog_places = missing_population_count + below_minimum_population_count + serving_places
```

`DEFAULT_RECOMMENDATION_LIMIT` is 10 for algorithm version `place-discovery-v1` and defines the
literal used by G1, FR7, the API, and `returned_count`. Changing it requires a new algorithm
version and PRD amendment.

New discovery endpoints follow the repository's existing FastAPI response convention: a bare JSON
success body and `{ "detail": string | list }` for errors. Lookup and recommendation success return
200. Invalid syntax or query bounds return FastAPI's existing 422 validation response. A catalog
hash, row-count, parse, or load failure returns 503 with `detail` equal to a safe
`CATALOG_UNAVAILABLE: ...` message. Origin, body-size, and hosted-boundary controls retain their
current `detail` responses. Fewer than 10 qualifying recommendations is a 200 success
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
- State and region are filters only. They are never profile score targets, match components, or
  members of the score denominator.
- Target weights are integers 1–5 with default 3. Total match divides present weighted similarity
  by the full profile-target weight, so missing candidate fields contribute zero without shrinking
  the denominator.
- Components record normalization bounds and target/candidate clipping; explanations disclose
  clipping and preserve raw values.
- A validated profile has at least two supported non-region targets after exemplar resolution.
- Recommendations order by total weighted match descending, present match-component count
  descending, then canonical `place_id` ascending.
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
independent review of `795eabc` confirmed the earlier corrections and found five remaining blocking
contracts: lookup did not expose exemplar eligibility; the profile entry rule contradicted its
post-resolution minimum; sparse score ties fell through to place identity; catalog diagnostics did
not account for below-threshold population; and the catalog-load budget had no failure semantics.
This revision closes those contracts and makes clipped-edge equality explicit. One final independent
verification review is required before implementation starts. The first verification attempt on
`a2749c7` did not run because the Claude provider returned account exhaustion; it is recorded as
incomplete, not as approval.

## As built

- **Profile exclusions.** `SearchProfile.exclude_places` (PRD FR2 "exclusions", FR10) lists towns the
  user marked **Not for me**. They are removed before constraint evaluation and counted in
  `user_excluded_count`. An unknown identifier is a 422; an example town cannot also be excluded.
- **Unknown-constraint counts** are computed over recommendable candidates only, so
  `recommendable_with_unknown_constraints_count` never exceeds `recommendable_count`.
- **State and region filters** appear in `known_constraint_exclusions` with ids `state_include`,
  `state_exclude`, `region_include`, and `region_exclude`. They are never match components.
- **Catalog.** `src/lifescape/data/place-catalog.csv.gz` and its manifest are built by
  `scripts/build_place_catalog.py` from the 2024 Census Gazetteer and ACS 2020–2024 5-year
  table-based summary files (B01003, B25077, B08301, B15003, B01001). Puerto Rico is outside the
  first catalog. Field definitions, hashes, per-field coverage (all at least 99% of the serving
  universe), and winsorization bounds are in the manifest.
- **Presentation.** Reasons and trade-offs are generated from component data by fixed templates
  in `DiscoveryService._explain`; tests assert every number in the prose appears in a component.

