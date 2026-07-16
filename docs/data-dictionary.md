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
