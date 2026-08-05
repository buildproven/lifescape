# ADR: Keep AI discovery outside the decision evidence boundary

## Status

Accepted for the AI-assisted discovery MVP.

## Decision

Add a local `SearchBrief` and `ResearchPacket` domain that represent user intent,
candidate leads, and proposed evidence separately from `ObservationRecord`. A packet
contains discovery material and its review state; it cannot be supplied to
`execute_run`. A packet can produce an engine-compatible observation only through a
pure promotion operation that requires a complete `SourceRecord`, an explicit metric,
an allowed A/B source, and a reviewed state. Promotion records the packet ID and the
human reviewer identity in its local audit result. An AI cannot be the sole reviewer
of its own packet output.

The local web API may create and inspect packets, but it does not introduce hosted
storage or let AI material mutate the engine's evidence CSV. `execute_run` remains
the only authority for gates, eligibility, scoring, sensitivity, persistence, and
reports.

## Context

The product must turn a user's preferences and exemplar towns into candidate leads,
but model-generated and web-discovery claims are Tier C until independently checked.
Several desired facts are property or address specific, so their absence cannot block
early town discovery; they remain visible as finalist-verification requirements.

## Alternatives considered

1. Let the discovery agent emit rows directly into the evidence CSV. Rejected:
   it would collapse unreviewed claims and scoring evidence into one path.
2. Add a hosted shared town database first. Rejected: data schema, refresh policy,
   licensing, and promotion workflow are not yet proven.
3. Keep discovery as unstructured app notes. Rejected: provenance and promotion
   checks would be duplicated across callers and could not be tested reliably.

## Invariants

- Tier C and non-promoted packet material never affects a gate or score.
- Promotion preserves source URL, publisher, dates, geography, confidence, and
  metric identity; incomplete records fail visibly. It rejects AI summaries, search
  pages, and other discovery URLs as evidence, and it rejects source geography that
  does not exactly match the candidate/metric geography required by source policy.
- Candidate discovery and finalist verification have separate states and UI labels.
- No silent proxy, midpoint, geography substitution, or inferred evidence is allowed.
- Unknown critical evidence continues to block at `execute_run`; allowing a discovery
  lead with missing finalist evidence does not relax decision-time gating.
- All packet state is local/session-scoped for this MVP.

## Rollback

The packet module and its API routes can be removed without migrating engine
evidence, SQLite run history, or scoring configuration because packets are not part
of `execute_run` input or persistence. Packet JSON is never an accepted engine-loader
format, even if a local packet artifact remains after route removal.

## Verification

- Unit tests reject Tier C and incomplete promotion attempts.
- A promoted ready record passes the same source-policy validation as manual evidence.
- API tests show discovery leads/readiness but prove they cannot invoke scoring.
- Tests prove promotion retains packet-origin/reviewer audit metadata and that engine
  loaders reject packet-format files.
- Existing engine, browser-journey, source-policy, Ruff, and mypy gates remain green.
