# API and data-source inventory

The core engine still accepts the existing manual CSV contract. The local research packet
workflow now adds review-gated ACS and NOAA adapter output; no fetched value bypasses the
approval boundary.

| Source | Dataset/API | Purpose | Access | Auth | Rate limit | Terms | Geography | Freshness | Reliability | Fallback | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Manual evidence | Wide CSV contract | All configured metrics | Local file | None | None | Operator verifies | Town | Per row | Depends on cited source | None | Implemented |
| Census | ACS 5-Year Data Profile | Demographics and derived distress proxy | REST | API key | Provider-defined | Review before use | Town | Dataset-defined | High | Manual evidence | Implemented: adapter + review queue |
| BLS | LAUS/QCEW | Labor market | REST/download | Optional key | Provider-defined | Review before use | County/metro | Dataset-defined | High | Download | Planned |
| BEA | Regional data | Income/economy | REST | Key | Provider-defined | Review before use | County/metro | Dataset-defined | High | Download | Planned |
| FHFA | HPI | Long-run housing trend | Download | None | None | Review before use | Metro/division | Dataset-defined | High | None | Planned |
| CMS | Care Compare | Hospital quality | API/download | Varies | Provider-defined | Review before use | Facility | Dataset-defined | High | Manual | Planned |
| HRSA | Shortage areas | Healthcare access | API/download | Varies | Provider-defined | Review before use | Area | Dataset-defined | High | Manual | Planned |
| NOAA | Global Summary of the Year (GSOY) | Explicit station/year snowfall | REST | None | Provider-defined | Review before use | Station | Dataset-defined | High | Manual evidence | Implemented: adapter + review queue |
| FEMA | Flood services | Flood risk | Service/manual | Varies | Provider-defined | Review before use | Parcel/area | Dataset-defined | High | Manual map review | Planned |
| FCC | Broadband map | Availability | Download/manual | Varies | Provider-defined | Review before use | Location/area | Dataset-defined | High | Provider check | Planned |
