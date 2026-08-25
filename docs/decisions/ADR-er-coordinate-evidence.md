# ADR: Require versioned coordinate evidence before ER routing

## Status

Accepted for BUI-438. Routing-provider selection remains in BUI-439.

## Decision

Lifescape will prepare ER route endpoints from two explicit evidence sources:

- The origin is the internal point for one Census place GEOID in a versioned U.S. Census
  Gazetteer file. It is a representative point for the place. It is not a home address, a
  downtown point, or a population-weighted centroid.
- An eligible destination is a CMS Hospital General Information record whose `Emergency
  Services` field is `Yes`. Lifescape retains the CMS facility ID, full address, dataset
  version, retrieval date, source URL, and response checksum.
- The destination coordinate is one unambiguous U.S. Census Geocoder match for the retained
  CMS address. Lifescape retains the original and matched addresses, pinned benchmark,
  retrieval date, request URL, response checksum, and match status.

Moving source aliases that include `Current` are invalid. A research operator must pin the
Gazetteer year, CMS release, and Census Geocoder benchmark. Missing, malformed, duplicate, or
ambiguous source records produce missing coordinate evidence.

`build_route_endpoints` returns no endpoints until both coordinate records validate. This
slice does not select a routing provider and cannot emit `er_drive_minutes`.

## Why

CMS publishes hospital identity, address, and emergency-service status but does not publish a
route from a retirement location. The current live connector contract stores provider lookup
strings, not route endpoint evidence. Inferring a town center, geocoding without retaining the
response, or substituting straight-line distance would hide material uncertainty.

The Census Bureau describes Gazetteer coordinates as representative latitude and longitude
coordinates for named geographic entities. Its Geocoder returns coordinates calculated from
address ranges and identifies the benchmark used. These semantics are useful only when the
source version and response remain visible.

## Consequences

- A Census place internal point is a reproducible comparison origin, not a household-specific
  travel claim. A later property workflow must use a separately governed address origin.
- A single geocoder match proves a reproducible service response. It does not prove entrance,
  driveway, or emergency-department-door precision.
- BUI-331 remains blocked until BUI-439 selects and provisions a routing backend with route,
  graph/provider, request, response, failure, refresh, licensing, and cost policy.
- A future routing connector must accept `RouteEndpoints` and must reject straight-line or
  fallback-speed estimates.

## Primary sources

- CMS Hospital General Information dataset: <https://data.cms.gov/provider-data/dataset/xubh-q36u>
- Census Gazetteer files: <https://www.census.gov/geographies/reference-files/time-series/geo/gazetteer-files.html>
- Census Geocoding Services API: <https://geocoding.geo.census.gov/geocoder/Geocoding_Services_API.html>
