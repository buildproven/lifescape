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

