# Architecture

Lifescape has two layers with a one-way boundary: a **discovery layer** that finds and explains
candidate towns, and an **evidence layer** that decides. Discovery output never flows into the
evidence layer except as a user's choice of which towns to verify.

```text
                    advisory                          authoritative
  ┌──────────────────────────────────────┐   ┌─────────────────────────────────────┐
  │ Census catalog ─▶ DiscoveryService   │   │ reviewed CSV ─▶ execute_run         │
  │ (hash-verified)    search(profile)   │   │ gates → scoring → sensitivity → DB  │
  │        │                │            │   │            │                        │
  │   /api/places   /api/place-          │   │       /api/run, reports             │
  │                 recommendations      │   │            ▲                        │
  └────────┬─────────────────┬───────────┘   └────────────┼────────────────────────┘
           ▼                 ▼                            │
      browser: Preferences → Boundaries → Matches → Shortlist ─▶ Verify (choose towns)
               (scenario saved only in this browser's localStorage)
```

- `discovery.py` owns profile validation, catalog integrity, normalization, similarity, ranking,
  explanations, and diagnostics (ADR-place-discovery-contract). It imports nothing from
  `pipeline`, `evidence`, `gates`, `scoring`, `sensitivity`, `db`, or `reports`; a test enforces
  this.
- `scripts/build_place_catalog.py` builds the packaged catalog reproducibly from pinned official
  Census files and writes the manifest (source hashes, field definitions, coverage, bounds).
- `web.py` loads the catalog once at `create_app`, serves the two discovery routes, and keeps the
  evidence routes unchanged. A catalog failure degrades discovery only (`503 CATALOG_UNAVAILABLE`).
- `static/scenario.js` validates, migrates, and backs up the saved search; `static/app.js` renders
  the journey and calls `/api/run` only with towns that have reviewed evidence.

## Evidence engine

The engine is a deterministic local pipeline:

```text
frozen YAML + manual CSV
          │
          ▼
strict config and evidence validation
          │
          ├── rejected source/geography/freshness → typed error
          ▼
SQLite provenance → hard gates → eligible set → normalization/scoring
                                             │
                                             ├── seeded sensitivity
                                             └── Markdown/CSV reports
```

Configuration is immutable after validation. The run ID hashes canonical configuration and evidence content. Gates execute before ranking. The reporting path consumes the same evaluated domain records that are persisted, so it cannot silently reinterpret evidence.

The supported version-one interface ends at the reviewed CSV boundary. The connector protocol
under `src/lifescape/connectors`, ACS/NOAA adapters, and research-packet workflow are experimental;
their fetched observations remain review-pending until a human approves them.

## Experimental AI-assisted discovery boundary

The retained experimental API may send a `SearchBrief` to an opt-in discovery provider and
receive a session-local `ResearchPacket`. The packet contains candidate leads,
rationales, caveats, and optional discovery links only. It is not an evidence CSV or
an engine input.

```text
SearchBrief → optional Claude discovery → ResearchPacket (Tier C)
                                              │
                          selected leads → ACS/NOAA adapter fetch
                                              │
                              human-reviewed A/B source record
                                              ▼
                                      promotion validation
                                              │
                                      normal evidence CSV
                                              │
                                              ▼
                                      execute_run (only when complete)
```

`execute_run` remains the only authority for gates, scoring, sensitivity, persistence,
and reports. A promotion validates one source record without changing any current run.
Tier C material, incomplete provenance, and geography mismatches fail before a record
can become an `ObservationRecord`.
