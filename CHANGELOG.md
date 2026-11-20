# Changelog

## Unreleased

### Fixed

- **Catalog rebuild reads a committed Census geocoder response (BUI-1722).**
  `scripts/build_place_catalog.py` now reads the hospital geocoder results from
  `data/place-catalog/census-geocoder-response.csv` and calls Census only with
  `--refresh-geocoder`. `--verify` runs offline, fails when the committed response's SHA-256 is
  not the one in the manifest (naming both), and requires the rebuilt catalog and manifest to
  match byte for byte. The earlier response was not kept, so the catalog was rebuilt once from a
  fresh one. This time Census re-matched every address the same way: the response has the same
  SHA-256 as before (`0c9cd6ab…e5f6`), and no hospital-distance value changed. A later
  `--refresh-geocoder` can shift some hospital-distance values if Census re-matches addresses.

- **Research shortlist lists decision-ready towns (BUI-1995).** `lifescape research-report` no
  longer stops with an error when a town has validated evidence for every critical gate. The
  shortlist now puts that town in a "Decision ready" bucket and counts it in the field summary.

- **Whole numbers round the same in the browser (BUI-1487).** Miles, days, inches, feet, and
  density now round exact .5 ties half to even in the browser, as the server does, so a match
  card reads the same before and after it is re-rendered (for example 2.5 miles reads "2 miles").

- **Match list and checklist name every source (BUI-1488).** The match-list notice and the
  research checklist intro now name Census, NOAA climate normals, place character (USGS and
  Natural Earth), and CMS hospital data, instead of saying the matches come from Census data.

### Added

- **Hospital provenance says how the hospital was located (BUI-1491).** Each place's
  `hospital_source` now carries a location method: `census_geocoder` (street address matched by
  the Census Geocoder) or `zcta_centroid` (ZIP Code Tabulation Area centroid), so a user can see
  how precise the hospital distance is. Discovery results show it as
  `hospital_source.location_method`, and discovery refuses to load a catalog whose
  `hospital_source` has any other method or none. The catalog was rebuilt from the same pinned inputs as catalog
  `us-places-acs2024-noaa1991-2020-character-hospital-v6`; no distance or other value changed
  (26,862 places use a geocoded hospital, 5,179 a ZCTA centroid).

- **Reject a fetched value from chat (BUI-2017).** The MCP `reject_evidence` tool records the
  named owner's rejection of one fetched value, with a reason, in its research packet.
  `approve_evidence` then refuses that value. A reason that is only spaces, or under eight
  characters after spaces are removed, is now refused.

- **Product tour video.** The public landing page shows a 40-second recorded walkthrough of the
  local app (`static/tour.mp4`), with a poster and a text list of the steps.

## 1.1.0 — 2026-10-20

### Fixed

- **Verification preview no longer flashes empty (BUI-1462).** The "What verification checks" panel stays hidden until its list of critical facts has loaded, and the Find places button is disabled in the served HTML (PR #58).

### Changed

- **Hospitals located by street address (BUI-1444).** Emergency hospitals are now placed by the
  Census Geocoder at their street address, with the pinned `Public_AR_Census2020` benchmark,
  instead of at their ZIP Code centroid. The ZIP Code centroid is used only for a facility the
  geocoder cannot match. Facilities left out fell from 103 to 23 of 4,435 (for example FLOWERS
  HOSPITAL, Dothan, AL, which has a PO Box ZIP Code), and large rural ZIP Codes no longer place a
  hospital far from its town. Catalog is now
  `us-places-acs2024-noaa1991-2020-character-hospital-v5`.

### Added

- **Match bands and feel summaries (BUI-1397).** Match cards and the comparison show a coarse
  band instead of a percentage: **Strong** when the total match is 0.85 or more, **Good**
  from 0.70 to below 0.85, and **Possible** below 0.70. Each match card also opens with a one-line
  summary of the town built only from its known catalog values; a missing value is not
  mentioned and is never imputed. Shortlists saved before bands existed get their band from the
  saved total match.
- **Hospital-access quality (BUI-1111).** Discovery now also matches on straight-line miles to the
  nearest emergency-capable hospital in the pinned CMS Hospital General Information file. Each
  town keeps the facility name as provenance and the match card names the facility and the miles.
  The distance is not drive time, uses the facility's ZIP Code centroid, stays null when no
  hospital is known, and never affects a gate or evidence-backed score. Catalog is now
  `us-places-acs2024-noaa1991-2020-character-hospital-v4`.
- **Place-character qualities (BUI-1396).** Discovery now also matches on elevation, distance to
  an ocean or Great Lake, distance to a metro of 1,000,000+ people, and whether the town is a
  suburb of one, so a lake-town example such as Traverse City, MI no longer returns an inland
  Minneapolis suburb first. Values come from public Natural Earth, USGS GNIS, and Census files;
  a value stays missing when its source has none. Catalog is now
  `us-places-acs2024-noaa1991-2020-character-v3`.
- **Climate qualities (BUI-1107).** Discovery now also matches on four NOAA 1991–2020 climate
  normals: freezing nights, hot days (90°F+), annual precipitation, and annual snowfall. Each
  town takes the nearest NOAA station within 30 miles, the Why panel names the station and
  distance, and a value stays missing (never zero) when no station qualifies. Two new one-tap
  styles, **Mild winters** and **Four seasons**. Catalog is now `us-places-acs2024-noaa1991-2020-v2`.

- **Side-by-side comparison.** With two or more kept towns, the shortlist shows a table of all six
  Census qualities and the discovery match band. Unknown values read "Unknown", never imputed.
  Climate rows appear with the Census rows.
- **Print or save as PDF** for the shortlist.
- **Official-source links** in the research checklist (Census profile, Medicare Care Compare, FCC
  broadband map, FEMA flood map, NOAA climate normals) for each finalist.

## 1.0.0 — 2026-10-04

First complete release. Lifescape finds U.S. places from towns you like, helps you keep a
shortlist, and then tests finalists against reviewed evidence.

### Added

- **Place discovery.** Deterministic, explainable town matches from a packaged, hash-verified
  catalog of 32,041 U.S. places built from the 2024 Census Gazetteer and ACS 2020–2024 five-year
  files (six qualities; recommendations from the 10,215 places with 2,500 or more people).
- **Guided local app.** One-click example, one-tap styles, optional fine-tuning and limits, ten
  match cards with reasons, trade-offs, unknowns, and **Why this place?**, Keep / Not for me /
  Unsure, and a shortlist that survives reloads with backup, export, and recovery.
- **Evidence handoff.** Per-finalist evidence state, a downloadable research checklist, advanced
  CSV import, and a comparison that runs only with reviewed evidence for at least two towns.
- **Traceability.** `docs/traceability.md` links every PRD requirement to design, code, and tests,
  enforced by `tests/test_traceability.py`.
- One-command install: `uvx --from git+https://github.com/buildproven/lifescape lifescape app`.

### Changed

- The primary journey is discovery first; reviewed CSV import is an advanced path. Reports save to
  `~/Lifescape` by default.
- Experimental Claude lead discovery now drops duplicate, exemplar, and excluded towns, requires at
  least eight usable leads, records the model id, and validates packets on the model.

### Not included in 1.0

Connectors for routing and broadband, evidence contradiction tracking, scenario-to-scenario
durability comparison, hosted accounts, and the five-household usability pilot. See
`docs/limitations.md`.
