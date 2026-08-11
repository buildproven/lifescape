# ADR: Defer a shared evidence corpus until promotion is proven

## Status

Accepted for the intent-driven discovery slice.

## Decision

Lifescape remains session-local while the adapter-to-review workflow is validated.
The product accepts user intent and optional exemplar towns, retrieves public-source
observations where a reproducible geography is configured, and stores only approved
records in the existing evidence CSV/run path. It does not create a shared database,
cache, or corpus in this slice.

## Why

The current workflow is still proving several contracts at once:

- an adapter observation is distinct from a discovery lead;
- a reviewer decision is distinct from a fetched value;
- source geography and observation dates survive export;
- stale, rejected, missing, and finalist-only metrics remain blocking; and
- a refresh must not silently replace a previously reviewed claim.

Persisting claims before these behaviors are exercised would preserve incompatible
records and make later source-policy changes look like trustworthy history.

## Boundaries

- `ResearchPacket` and fetched observations are session-scoped for the local MVP.
- `execute_run` remains the only gate, score, persistence, and report authority.
- Discovery material never satisfies a gate or contributes to a score.
- ACS and NOAA adapter output may be displayed before review, but only approved,
  complete, metric-compatible observations can enter a decision run.
- NOAA station evidence remains station evidence; the system never infers a town
  aggregate or a nearest station.
- FCC broadband, CMS/route-time, and property/neighborhood checks remain finalist
  verification work and do not become discovery prerequisites.

## Revisit criteria

Create a bounded corpus proposal only after the pilot records:

1. repeated deterministic adapter fetches with stable source/checksum metadata;
2. approved and rejected review decisions for the same metric and town;
3. stale/refresh behavior that does not overwrite history;
4. explicit incompatibility handling when source schemas or geography change; and
5. a source-licensing and retention decision for every persisted publisher.

Until all five are evidenced, a local-only packet is the correct product behavior.
