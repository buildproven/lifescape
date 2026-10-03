# API and data-source inventory

Place discovery runs offline from a packaged catalog built from Census bulk files. The reviewed CSV contract remains the evidence boundary. Research-packet, ACS, and NOAA acquisition is
experimental and absent from the primary interface; no fetched value bypasses the approval
boundary.

| Source | Dataset/API | Purpose | Access | Auth | Rate limit | Terms | Geography | Freshness | Reliability | Fallback | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Census Gazetteer | 2024 national places file | Place identity, land area, centroid | Bulk download (build time) | None | None | Public domain | Place | 2024 | High | None | Implemented (packaged catalog) |
| Census ACS | 2020-2024 5-year table-based summary files B01003, B25077, B08301, B15003, B01001 | Discovery fields | Bulk download (build time) | None | None | Public domain | Place | Dec 2025 release | High | None | Implemented (packaged catalog) |
| Manual evidence | Wide CSV contract | All configured metrics | Local file | None | None | Operator verifies | Town | Per row | Depends on cited source | None | Implemented |
| Census | ACS 5-Year Data Profile | Demographics and derived distress proxy | REST | API key | Provider-defined | Review before use | Town | Dataset-defined | High | Manual evidence | Experimental adapter + review queue |
| BLS | LAUS/QCEW | Labor market | REST/download | Optional key | Provider-defined | Review before use | County/metro | Dataset-defined | High | Download | Planned |
| BEA | Regional data | Income/economy | REST | Key | Provider-defined | Review before use | County/metro | Dataset-defined | High | Download | Planned |
| FHFA | HPI | Long-run housing trend | Download | None | None | Review before use | Metro/division | Dataset-defined | High | None | Planned |
| CMS | Care Compare | Hospital quality | API/download | Varies | Provider-defined | Review before use | Facility | Dataset-defined | High | Manual | Planned |
| HRSA | Shortage areas | Healthcare access | API/download | Varies | Provider-defined | Review before use | Area | Dataset-defined | High | Manual | Planned |
| NOAA | Global Summary of the Year (GSOY) | Explicit station/year snowfall | REST | None | Provider-defined | Review before use | Station | Dataset-defined | High | Manual evidence | Experimental adapter + review queue |
| FEMA | Flood services | Flood risk | Service/manual | Varies | Provider-defined | Review before use | Parcel/area | Dataset-defined | High | Manual map review | Planned |
| FCC | Broadband map | Availability | Download/manual | Varies | Provider-defined | Review before use | Location/area | Dataset-defined | High | Provider check | Planned |

## Local application routes

| Route | Purpose | Notes |
|---|---|---|
| `GET /api/places?query=&limit=` | Place lookup with `serving_eligible` and catalog values | 2–120 character query, limit 1–20 |
| `POST /api/place-recommendations` | Stateless discovery calculation | Local origin required, 64 KB limit, 422 on invalid profile, 503 `CATALOG_UNAVAILABLE` |
| `GET /api/bootstrap` | Evidence benchmark, metric details, discovery summary | |
| `POST /api/evidence/inspect`, `POST /api/run`, `GET /api/downloads/...` | Evidence import and the strict comparison | 5 MB import limit |

All routes return 404 when `hosted_demo=True`.

