# ADR: Make reviewed local comparison the Lifescape v1 product

## Status

Superseded by `docs/prd/lifescape-place-discovery.md` for BUI-334. The evidence invariants below
remain active; reviewed CSV import is now an advanced path instead of Lifescape's product entrance.

## Decision

Lifescape v1 is one local comparison workflow. A person sets a household decision frame, imports
a reviewed evidence CSV for at least two towns, reviews completeness, runs the deterministic
engine, and downloads provenance-backed reports.

The supported v1 surface includes strict configuration and source-policy validation, critical
gates, ranking, sensitivity analysis, visible missing evidence, SQLite provenance, report export,
the synthetic benchmark, and the read-only hosted example.

AI discovery, research packets, live connectors, adapter review and promotion, evidence auditing,
and conditional research reports remain in the repository as experimental capabilities. They are
not exposed in the primary local interface and do not define v1 completion. Routing, broadband,
property, parcel, flood, and neighborhood provider work is deferred.

## Alternatives

- Complete every planned provider before release. Rejected because it turns one comparison tool
  into several acquisition and operations systems with separate credentials, terms, costs, and
  evidence semantics.
- Delete every experimental capability. Rejected because deletion adds regression risk and loses
  tested research work that can inform a later product decision.
- Keep discovery in the primary interface but label it optional. Rejected because it still makes
  evidence acquisition appear to be part of the supported user journey.

## Invariants

- `execute_run` remains the only decision authority.
- Unknown critical evidence blocks a town.
- Tier C material cannot affect a gate or score.
- Synthetic evidence remains visibly synthetic.
- Reviewed CSV evidence retains source provenance, dates, geography, confidence, and missing
  values.
- Lifescape compares evidence; it does not make a purchase recommendation.

## Rollback

The experimental backend contracts remain intact. A future release can restore a research UI
only after a new product decision defines its supported sources, ownership, failure behavior, and
acceptance tests. Restoring the prior frontend commit is not sufficient to promote those tools.

## Verification

- The local page promotes reviewed CSV import and contains no research-acquisition controls.
- A browser test imports a reviewed fixture, selects towns, runs the comparison, and downloads
  report and provenance artifacts at mobile and desktop widths.
- Existing research API tests continue to protect the retained experimental contracts.
- Tests, Ruff, mypy, benchmark, package, browser, frontend, and security gates pass at the shipped
  revision.
