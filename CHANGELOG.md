# Changelog

## Unreleased

### Added

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
  Census qualities and the match percentage. Unknown values read "Unknown", never imputed. Climate rows appear with the Census rows.
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
