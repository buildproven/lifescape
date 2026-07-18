# Known limitations

- All benchmark values are synthetic; no real town conclusion is supported.
- Public data connectors and integration tests are Milestone 2 work. One connector (Census ACS, `education_attainment`) exists and is wired into a dedicated `retire live-run` command (`config/research_brief.live.yaml`); `retire benchmark` is unaffected and still uses only synthetic evidence. `education_attainment` is not a critical gate, so a real `live-run` today can only affect scoring, never gate blocking, until more connectors exist. Connectors have no retry logic; a transient failure degrades that (place, metric) to missing evidence for the run.
- Candidate discovery and research agents are not implemented.
- Confidence aggregation and contradiction tracking are deferred to Milestone 3; Milestone 1 enforces high confidence at gates.
- Neighborhood, property, mapping, scouting, future-self, and regret workflows are deferred to later milestones.
- Source retrieval recency and metric-specific observation age are enforced independently. Complex observation intervals still use one explicit effective observation date supplied by the evidence curator.
- Annual carrying-cost and priority-to-weight personalization are deferred until property-level evidence exists; Milestone 1 applies the profile's maximum purchase budget directly to the purchase-feasibility gate.
- Percentile scores are relative to the eligible candidate set and are not absolute quality claims.
- The engine produces comparison artifacts, not a `BUY` recommendation.
