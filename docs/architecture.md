# Milestone 1 architecture

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

The connector protocol is defined under `src/lifescape/connectors`; live connector implementations are deferred to Milestone 2.

## AI-assisted discovery boundary

The local app may send a user-approved `SearchBrief` to an opt-in discovery provider
and receive a session-local `ResearchPacket`. The packet contains candidate leads,
rationales, caveats, and optional discovery links only. It is not an evidence CSV or
an engine input.

```text
SearchBrief → optional Claude discovery → ResearchPacket (Tier C)
                                              │
                              human-reviewed A/B source record
                                              ▼
                                      promotion validation
                                              │
                                              └── a future normal evidence run
```

`execute_run` remains the only authority for gates, scoring, sensitivity, persistence,
and reports. A promotion validates one source record without changing any current run.
Tier C material, incomplete provenance, and geography mismatches fail before a record
can become an `ObservationRecord`.
