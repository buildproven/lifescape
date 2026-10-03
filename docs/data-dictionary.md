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

Catalog `us-places-acs2024-v1`, normalization `discovery-winsorized-minmax-v1`, algorithm
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

Identity: `place_id` (7-digit state+place FIPS), `name`, `state`, `region` (Census region), plus
`land_area_sqmi`, `latitude`, `longitude`. The manifest `src/lifescape/data/place-catalog.manifest.json`
records source URLs, SHA-256 hashes, per-field coverage, and bounds.
