# Known limitations

- All benchmark values are synthetic; no real town conclusion is supported.
- Version one compares reviewed CSV evidence; it does not acquire evidence. Public-source
  adapters, research packets, and `lifescape live-run` remain experimental and are absent from
  the supported local interface.
- The experimental Census ACS connector supports `education_attainment` and a locally derived
  `distress_index`. NOAA GSOY supports explicit station/year snowfall. Adapter output is never
  decision evidence until a named human approves it. Connectors have no retry logic; a transient
  failure remains missing evidence.
- No free, town-level public API was found for `median_sale_price`, `flood_risk_score`, or `one_level_inventory_count` as of 2026-07 (Zillow's public API is discontinued; FEMA's flood API is not publicly accessible without a paid third-party wrapper; real-estate listing inventory is inherently commercial/MLS-adjacent data). These metrics are expected to stay manually curated.
- Optional Claude discovery can suggest unverified candidate-town leads from a user
  SearchBrief and zero, one, or two exemplar towns. It has no authority to provide decision
  evidence, clear a gate, or rank a town. Claude does not receive evidence imports. The
  experimental API can fetch configured ACS/NOAA records, retain a named human's approve/reject
  decision, and export only approved records into the normal evidence CSV contract. A research
  packet cannot enter a run until every candidate has complete approved critical evidence.
- FCC broadband availability is location-level, and CMS hospital facts do not establish a
  household's route-time outcome. Both belong to finalist, address-aware verification after
  a town clears discovery and evidence review; neither is a discovery gate or a town-level
  proxy in the current engine. Lifescape can now retain a versioned Census place internal point,
  CMS emergency-capable hospital address, and pinned Census Geocoder match as route-endpoint
  evidence. It still has no approved routing backend and emits no `er_drive_minutes` value.
  Routing and broadband automation are deferred and do not block the reviewed-CSV v1 contract.
- Confidence aggregation and contradiction tracking are deferred to Milestone 3; Milestone 1 enforces high confidence at gates.
- Neighborhood, property, mapping, scouting, future-self, and regret workflows are deferred to later milestones.
- Source retrieval recency and metric-specific observation age are enforced independently. Complex observation intervals still use one explicit effective observation date supplied by the evidence curator.
- Annual carrying-cost and priority-to-weight personalization are deferred until property-level evidence exists; Milestone 1 applies the profile's maximum purchase budget directly to the purchase-feasibility gate.
- Percentile scores are relative to the eligible candidate set and are not absolute quality claims.
- The engine produces comparison artifacts, not a `BUY` recommendation.
