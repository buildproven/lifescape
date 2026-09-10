# ADR: Deliver BUI-334 through one typed discovery service

## Status

Proposed for the BUI-334 task 2.0 implementation slice.

## PRD trace

`docs/prd/lifescape-place-discovery.md`: G1, G3, G5, FR2–FR9, FR14,
Reproducibility, Privacy, and Evidence integrity.

## Decision

This document supplements, and does not replace, the accepted contract in
`docs/decisions/ADR-place-discovery-contract.md`. That ADR remains authoritative for
`PlaceDiscoveryService.search(SearchProfile) -> DiscoveryResult` and the discovery
algorithm. The first slice adds the following read-only adapter operations around that
same service for the approved local HTTP resources:

```text
PlaceDiscoveryService.validate(profile) -> SearchProfile
PlaceDiscoveryService.lookup(query, limit) -> PlaceLookupResult
PlaceDiscoveryService.search(profile) -> DiscoveryResult
```

The service owns profile validation, catalog loading, eligibility, normalization,
exemplar resolution, hard-constraint handling, missing values, deterministic ordering,
structured explanations, and version metadata. FastAPI is an adapter over this interface.
It must not calculate scores, load rows per request, or call
`execute_run`.

The service returns a discovery result type that is not an `ObservationRecord` and has no
conversion path into gate, scoring, or evidence inputs. The first slice exposes the result
through the stateless local API. CLI parity is a later proposal and is not part of this ADR.

The catalog loader accepts a checked-in versioned catalog and manifest. It hashes the catalog
artifact and compares that digest with the manifest's recorded output hash, and separately
compares the parsed row count with the manifest's recorded row count, before exposing any row.
A missing or invalid catalog leaves the local app available but makes discovery endpoints return
a visible `CATALOG_UNAVAILABLE` 503. Tests use an independent fixture catalog; synthetic fixture
values are never presented as real-world evidence.

## Alternatives

- Put scoring in FastAPI separately. Rejected because the API caller could disagree with the
  domain on null handling or tie-breaking.
- Reuse `ObservationRecord`. Rejected because discovery data is advisory Tier C material and
  must not enter the evidence authority boundary.
- Keep using the model-backed research provider. Rejected because it is non-deterministic,
  credential-dependent, and can produce unsupported facts.

## Invariants

- A fixed profile, catalog version, algorithm version, and normalization version/configuration
  produce byte-identical ordered results.
- Unknown discovery values neither pass constraints nor contribute similarity.
- A known failed constraint excludes a candidate; an exemplar is never recommended again.
- A candidate needs two supported non-region match components.
- Discovery results cannot be passed to `execute_run`, gates, or evidence scoring.
- Hosted mode returns the existing explicit 404 boundary.
- Origin and request-size controls apply before recommendation calculation.

## Rollback and verification

The slice is additive. Remove the discovery adapter, service, schemas, and fixture without
changing the existing evidence routes or `execute_run`. Verify with the focused discovery
module/API tests, Ruff, mypy, and the existing full test suite.
