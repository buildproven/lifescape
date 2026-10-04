# Known limitations

## Discovery

- Discovery compares six Census-derived qualities only: population, median home value, population
  density, car-light commute share, college-educated share, and older-adult share. It makes no
  claim about climate, healthcare, nature, airports, culture, or walkability. Car-light commute
  share is an ACS commute-mode proxy, not a walkability score.
- Recommendations and examples cover places with a known population of 2,500 or more (10,215
  places in catalog `us-places-acs2024-v1`). Smaller towns can be looked up and added to a
  shortlist by hand but receive no match score. Puerto Rico and other territories are outside this
  catalog.
- The catalog uses ACS 2020–2024 five-year estimates. It does not use margins of error, so close
  scores are not statistically distinguishable. Match percentages rank similarity; they are not
  probabilities or quality scores.
- Home value is the owner-occupied median, not a current listing price. Discovery never replaces
  the evidence run: a discovery match cannot clear a gate or change an evidence-backed score.
- Shortlists live in one browser's local storage. Clearing site data or switching browsers loses
  them; use **Export search as JSON** to keep a copy. There is no sync, account, or sharing.
- The five-household pilot in PRD section 8 has not been run (`docs/pilot/PILOT-PROTOCOL.md`). The
  success metrics are therefore `[unverified]`.

## Out of scope for 1.0 (decided)

These were evaluated and deliberately left out of the first release; none changes a 1.0 guarantee,
because missing evidence always blocks a finalist and never gets guessed.

- Routing, ER drive-time, and FCC broadband connectors: the PRD (section 3) excludes new
  connectors and routing services unless a user-visible slice needs them. Enter these values
  through the reviewed CSV.
- Evidence contradiction tracking and a separate confidence tier for locally derived composites:
  gates use a single high-confidence observation, as documented in `docs/source-policy.md`.
- Scenario-to-scenario "durability" comparison: 1.0 saves one local search and shortlist and
  exposes the engine's sensitivity analysis for finalists.
- Wiring live connectors into the benchmark command: `lifescape live-run` stays a separate,
  experimental command.

## Evidence

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
