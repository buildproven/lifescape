# Known limitations

- All benchmark values are synthetic; no real town conclusion is supported.
- Public data connectors, retries, and integration tests begin in Milestone 2.
- Candidate discovery and research agents are not implemented.
- Confidence aggregation and contradiction tracking are deferred to Milestone 3; Milestone 1 enforces high confidence at gates.
- Neighborhood, property, mapping, scouting, future-self, and regret workflows are deferred to later milestones.
- Freshness enforcement currently uses a policy-wide maximum; metric-specific freshness fields are stored but not independently enforced.
- Percentile scores are relative to the eligible candidate set and are not absolute quality claims.
- The engine produces comparison artifacts, not a `BUY` recommendation.

