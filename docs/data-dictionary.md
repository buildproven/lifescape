# Implemented data dictionary

All implemented metrics use town geography and a default 730-day freshness rule. Each metric also declares an inclusive valid numeric range in `config/metrics.yaml`; ingestion rejects non-finite or out-of-range evidence before gates or scoring. Critical metrics participate in gates and cannot be imputed.

| Metric ID | Description | Unit | Direction | Criterion | Gate | Missing treatment |
|---|---|---|---|---|---|---|
| `median_sale_price` | Median sale price | USD | Lower | Cost | Purchase feasibility | Blocking |
| `er_drive_minutes` | Drive time to emergency department | Minutes | Lower | Healthcare | Healthcare | Blocking |
| `broadband_mbps_down` | Download availability | Mbps | Higher | Daily life | Broadband | Blocking |
| `annual_snowfall` | Annual snowfall | Inches | Lower | Climate | Winter severity | Blocking |
| `flood_risk_score` | Comparative flood-risk index | Index | Lower | Climate | Hazard profile | Blocking |
| `distress_index` | Broad distress indicator | Index | Lower | Neighborhood | Distress profile | Blocking |
| `one_level_inventory_count` | One-level/adaptable listings | Listings | Higher | Daily life | Aging-in-place | Blocking |
| `trail_miles_within_30` | Trails within 30 minutes | Miles | Higher | Nature | None | Penalized |
| `restaurant_density` | Restaurants per 10,000 residents | Count | Higher | Daily life | None | Penalized |
| `education_attainment` | Bachelor's attainment | Percent | Higher | Community | None | Penalized |
| `volunteer_org_count` | Volunteer organizations | Count | Higher | Social | None | Penalized |
| `median_days_on_market` | Median market time | Days | Lower | Resilience | None | Penalized |
| `population_growth_10yr` | Ten-year population growth | Percent | Higher | Economic resilience | None | Penalized |
| `airport_drive_minutes` | Airport drive time | Minutes | Lower | Airport | None | Penalized |
| `winter_escape_score` | Winter escape flexibility | Index | Higher | Winter escape | None | Penalized |
| `water_access_drive_minutes` | Water-access drive time | Minutes | Lower | Water | None | Penalized |
| `sailing_season_months` | Practical sailing season | Months | Higher | Sailing | None | Penalized |

The full machine-readable definitions are in `config/metrics.yaml`.

## Discovery catalog fields (advisory, never evidence)

Catalog `us-places-acs2024-noaa1991-2020-character-hospital-v4`, normalization `discovery-winsorized-minmax-v1`, algorithm
`place-discovery-v1`. Values are `null` when missing; any negative ACS value (including the
`-666666666` and `-999999999` annotations) is stored as `null`. These fields are not
`ObservationRecord` values and never enter `execute_run`. Bounds are the 5th and 95th percentiles
(nearest rank) over places with population 2,500 or more.

| Field | Derivation | Unit | Source tables |
|---|---|---|---|
| `population` | `B01003_E001` | people | ACS B01003 |
| `median_home_value` | `B25077_E001` | USD | ACS B25077 |
| `population_density` | population ÷ Gazetteer `ALAND_SQMI` | people per sq mi | ACS B01003, Gazetteer |
| `car_light_commute_share` | (`B08301_E010` + `E018` + `E019` + `E021`) ÷ `B08301_E001` | percent of workers | ACS B08301 |
| `college_educated_share` | (`B15003_E022`–`E025`) ÷ `B15003_E001` | percent of adults 25+ | ACS B15003 |
| `older_adult_share` | (`B01001_E020`–`E025` + `E044`–`E049`) ÷ `B01001_E001` | percent of residents | ACS B01001 |
| `freezing_nights_per_year` | `ANN-TMIN-AVGNDS-LSTH032` at the nearest station within 30 mi | days per year | NOAA 1991–2020 normals |
| `hot_days_per_year` | `ANN-TMAX-AVGNDS-GRTH090` at the nearest station within 30 mi | days per year | NOAA 1991–2020 normals |
| `annual_precip_in` | `ANN-PRCP-NORMAL` at the nearest station within 30 mi | inches per year | NOAA 1991–2020 normals |
| `annual_snowfall_in` | `ANN-SNOW-NORMAL` at the nearest station reporting it within 30 mi; `null` otherwise, never `0` | inches per year | NOAA 1991–2020 normals |
| `elevation_ft` | `ELEV_IN_FT` of the GNIS populated place with the same name in the same state nearest the place centroid, within 5 mi; `null` otherwise. The only field that can be negative | feet above sea level | USGS GNIS 2021-08-25 archive |
| `water_distance_mi` | Great-circle miles from the centroid to the nearest ocean coastline or Great Lakes shore. Exact to shoreline segments within 60 mi, to shoreline points under 4 mi apart beyond. Inland lakes and rivers are not counted | miles | Natural Earth 1:10m v5.1.2 |
| `large_metro_distance_mi` | Great-circle miles to the nearest principal city of a metropolitan area with 1,000,000+ people; `0` for those cities. Principal cities are the places named in the metro title | miles | Gazetteer CBSA, ACS B01003 |
| `large_metro_suburb` | `1` when the place is not a principal city of such a metro and lies within 25 mi of one, else `0` (a distance rule, not a Census urban-area designation) | yes or no | Gazetteer CBSA, ACS B01003 |
| `hospital_distance_mi` | Straight-line (great-circle) miles from the centroid to the nearest hospital in the pinned CMS Hospital General Information file with `Emergency Services` = `Yes`, located at the centroid of its ZIP Code Tabulation Area; `null` when no located emergency hospital exists, never imputed. Not drive time | straight-line miles | CMS Hospital General Information (xubh-q36u), Gazetteer ZCTA |

Each place also carries a `climate_sources` provenance cell (station id, name, and miles for the
temperature, precipitation, and snowfall joins) and a `hospital_source` cell (CMS facility id,
facility name, and miles, matching `hospital_distance_mi`). The manifest records
`hospital_facilities`: emergency facilities read from the CMS file, how many a ZCTA centroid
located, and how many were left out because their ZIP Code is not a ZCTA.

Identity: `place_id` (7-digit state+place FIPS), `name`, `state`, `region` (Census region), plus
`land_area_sqmi`, `latitude`, `longitude`. The manifest `src/lifescape/data/place-catalog.manifest.json`
records source URLs, SHA-256 hashes, per-field coverage, and bounds.

### Recommendation card fields (derived, advisory)

Added to each discovery recommendation by `DiscoveryService.search`. Both come only from that
place's structured catalog values and total match; `total_match` and `match_percent` are unchanged.

| Field | Derivation |
|---|---|
| `match_band` | `Strong` when `total_match` ≥ 0.85, `Good` when ≥ 0.70 and below 0.85, `Possible` below 0.70. The app shows the band instead of a percentage. |
| `feel_summary` | One line (no newline) built from non-null values: size from `population`, then winters, summers, snow, shoreline distance, large-metro distance, and high elevation when those values exist and cross a threshold. A null value is never mentioned. Contains no `%` and no "match". |

Snapshots saved without these fields still load; the app derives the band from `total_match` and
omits the summary.
