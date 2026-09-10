# API and data-source inventory

Version one supports the local discovery catalog and reviewed CSV contract. Discovery values are
Tier C advisory material and never bypass the evidence approval boundary.

| Source | Dataset/API | Purpose | Access | Auth | Rate limit | Terms | Geography | Freshness | Reliability | Fallback | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Manual evidence | Wide CSV contract | All configured metrics | Local file | None | None | Operator verifies | Town | Per row | Depends on cited source | None | Implemented |
| Census | Gazetteer 2024 + ACS 2023 5-year table-based files | Versioned place-discovery catalog | Checked-in build artifact | None | Catalog manifest and SHA-256 | Incorporated place | 2023/2024 vintage | Official source; derived fields documented | Rebuild from pinned source URLs | Implemented for local discovery |
| Census | ACS 5-Year Data Profile | Demographics and derived distress proxy | REST | API key | Provider-defined | Review before use | Town | Dataset-defined | High | Manual evidence | Experimental adapter + review queue |
| BLS | LAUS/QCEW | Labor market | REST/download | Optional key | Provider-defined | Review before use | County/metro | Dataset-defined | High | Download | Planned |
| BEA | Regional data | Income/economy | REST | Key | Provider-defined | Review before use | County/metro | Dataset-defined | High | Download | Planned |
| FHFA | HPI | Long-run housing trend | Download | None | None | Review before use | Metro/division | Dataset-defined | High | None | Planned |
| CMS | Care Compare | Hospital quality | API/download | Varies | Provider-defined | Review before use | Facility | Dataset-defined | High | Manual | Planned |
| HRSA | Shortage areas | Healthcare access | API/download | Varies | Provider-defined | Review before use | Area | Dataset-defined | High | Manual | Planned |
| NOAA | Global Summary of the Year (GSOY) | Explicit station/year snowfall | REST | None | Provider-defined | Review before use | Station | Dataset-defined | High | Manual evidence | Experimental adapter + review queue |
| FEMA | Flood services | Flood risk | Service/manual | Varies | Provider-defined | Review before use | Parcel/area | Dataset-defined | High | Manual map review | Planned |
| FCC | Broadband map | Availability | Download/manual | Varies | Provider-defined | Review before use | Location/area | Dataset-defined | High | Provider check | Planned |
