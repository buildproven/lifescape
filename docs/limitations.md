# Known limitations

- All benchmark values are synthetic; no real town conclusion is supported.
- Public data connectors and integration tests are Milestone 2 work. The Census ACS connector supports `education_attainment` (direct pull-through) and `distress_index` (a derived proxy: the unweighted average of ACS poverty rate, unemployment rate, and vacant housing rate — not an official Census statistic; see `SourceRecord.title` on the resulting observation). Both are wired into a dedicated `lifescape live-run` command (`config/research_brief.live.yaml`); `lifescape benchmark` is unaffected and still uses only synthetic evidence. `distress_index` is the first live-fetched metric that can actually block a candidate (critical gate, threshold `<=7`); the other five critical gates (`purchase_feasibility`, `healthcare`, `broadband`, `winter_severity`, `hazard_profile`, `aging_in_place_supply`) have no live connector yet and still require manual evidence. Connectors have no retry logic; a transient failure degrades that (place, metric) to missing evidence for the run.
- No free, town-level public API was found for `median_sale_price`, `flood_risk_score`, or `one_level_inventory_count` as of 2026-07 (Zillow's public API is discontinued; FEMA's flood API is not publicly accessible without a paid third-party wrapper; real-estate listing inventory is inherently commercial/MLS-adjacent data). These metrics are expected to stay manually curated.
- Candidate discovery and research agents are not implemented.
- Confidence aggregation and contradiction tracking are deferred to Milestone 3; Milestone 1 enforces high confidence at gates.
- Neighborhood, property, mapping, scouting, future-self, and regret workflows are deferred to later milestones.
- Source retrieval recency and metric-specific observation age are enforced independently. Complex observation intervals still use one explicit effective observation date supplied by the evidence curator.
- Annual carrying-cost and priority-to-weight personalization are deferred until property-level evidence exists; Milestone 1 applies the profile's maximum purchase budget directly to the purchase-feasibility gate.
- Percentile scores are relative to the eligible candidate set and are not absolute quality claims.
- The engine produces comparison artifacts, not a `BUY` recommendation.
