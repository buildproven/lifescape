"""Build the packaged place-discovery catalog from official Census bulk files.

Inputs (all keyless, public, pinned by SHA-256 in the manifest):
- 2024 Census Gazetteer, places: identity, land area, centroid.
- NOAA U.S. Climate Normals 1991-2020 (annual/seasonal, by station): freezing nights, 90°F+ days,
  precipitation, snowfall, joined to each place by the nearest reporting station within 30 miles.
- ACS 2020-2024 5-year table-based summary files: B01003, B25077, B08301, B15003, B01001.

Usage:
    uv run python scripts/build_place_catalog.py            # build and write catalog + manifest
    uv run python scripts/build_place_catalog.py --verify   # rebuild; fail if output differs

Traceability: docs/decisions/ADR-place-discovery-contract.md, PRD FR4, FR5.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import math
import sys
import tarfile
import urllib.request
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

from lifescape.discovery import (
    CATALOG_COLUMNS,
    MINIMUM_SERVING_POPULATION,
    NORMALIZATION_VERSION,
    SCORED_FIELDS,
    STATE_REGIONS,
    nearest_rank_percentile,
)

ACS_BASE = "https://www2.census.gov/programs-surveys/acs/summary_file/2024/table-based-SF"
GAZETTEER_URL = (
    "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2024_Gazetteer/"
    "2024_Gaz_place_national.zip"
)
ACS_TABLES = ("b01003", "b25077", "b08301", "b15003", "b01001")
NOAA_URL = (
    "https://www.ncei.noaa.gov/data/normals-annualseasonal/1991-2020/archive/"
    "us-climate-normals_1991-2020_v1.0.1_annualseasonal_multivariate_by-station_c20230404.tar.gz"
)
NOAA_VINTAGE = "NOAA U.S. Climate Normals 1991-2020 v1.0.1 (annual/seasonal, by station)"
CLIMATE_RADIUS_MILES = 30.0
# Each group is joined to the nearest station that reports every variable in the group.
CLIMATE_GROUPS: dict[str, tuple[tuple[str, str], ...]] = {
    "temperature": (
        ("freezing_nights_per_year", "ANN-TMIN-AVGNDS-LSTH032"),
        ("hot_days_per_year", "ANN-TMAX-AVGNDS-GRTH090"),
    ),
    "precipitation": (("annual_precip_in", "ANN-PRCP-NORMAL"),),
    "snowfall": (("annual_snowfall_in", "ANN-SNOW-NORMAL"),),
}
CATALOG_VERSION = "us-places-acs2024-noaa1991-2020-v2"
ACS_VINTAGE = "ACS 2020-2024 5-year estimates (released December 2025)"
GAZETTEER_VINTAGE = "2024 Census Gazetteer"
DATA_DATE = "2024-12-31"
REPOSITORY = Path(__file__).resolve().parents[1]
CATALOG_PATH = REPOSITORY / "src/lifescape/data/place-catalog.csv.gz"
MANIFEST_PATH = REPOSITORY / "src/lifescape/data/place-catalog.manifest.json"
CACHE = REPOSITORY / ".cache/place-catalog"

CAR_LIGHT_VARIABLES = ("B08301_E010", "B08301_E018", "B08301_E019", "B08301_E021")
COLLEGE_VARIABLES = ("B15003_E022", "B15003_E023", "B15003_E024", "B15003_E025")
OLDER_ADULT_VARIABLES = tuple(f"B01001_E{n:03d}" for n in (*range(20, 26), *range(44, 50)))

# ACS marks suppressed, unreliable, or unavailable estimates with large negative annotation
# values (for example -666666666, -999999999). Every negative estimate becomes null.
SENTINEL_POLICY = "Every negative ACS estimate (including -666666666 and -999999999) becomes null."

FIELD_DEFINITIONS: dict[str, dict[str, str]] = {
    "population": {
        "label": "Population",
        "unit": "people",
        "definition": "Total population, ACS table B01003 (B01003_E001).",
    },
    "median_home_value": {
        "label": "Median home value",
        "unit": "USD",
        "definition": "Median value of owner-occupied housing units, ACS B25077 (B25077_E001).",
    },
    "population_density": {
        "label": "Population density",
        "unit": "people per square mile",
        "definition": "B01003_E001 divided by Gazetteer land area (ALAND_SQMI).",
    },
    "car_light_commute_share": {
        "label": "Car-light commute share",
        "unit": "percent of workers",
        "definition": (
            "Share of workers 16+ who take public transit, bicycle, walk, or work from home "
            "(B08301_E010 + E018 + E019 + E021) / B08301_E001. An ACS commute-mode proxy, "
            "not a walkability score."
        ),
    },
    "college_educated_share": {
        "label": "College-educated share",
        "unit": "percent of adults 25+",
        "definition": (
            "Adults 25+ with a bachelor's degree or higher "
            "(B15003_E022 + E023 + E024 + E025) / B15003_E001."
        ),
    },
    "older_adult_share": {
        "label": "Older-adult share",
        "unit": "percent of residents",
        "definition": "Residents aged 65+ (B01001_E020-E025 + E044-E049) / B01001_E001.",
    },
    "freezing_nights_per_year": {
        "label": "Freezing nights",
        "unit": "days per year",
        "definition": (
            "Average days per year with a daily low at or below 32°F, NOAA 1991-2020 normal "
            "ANN-TMIN-AVGNDS-LSTH032, from the nearest station within 30 miles."
        ),
    },
    "hot_days_per_year": {
        "label": "Hot days (90°F+)",
        "unit": "days per year",
        "definition": (
            "Average days per year with a daily high at or above 90°F, NOAA 1991-2020 normal "
            "ANN-TMAX-AVGNDS-GRTH090, from the nearest station within 30 miles."
        ),
    },
    "annual_precip_in": {
        "label": "Annual precipitation",
        "unit": "inches per year",
        "definition": (
            "Annual precipitation normal in inches, NOAA 1991-2020 ANN-PRCP-NORMAL, from the "
            "nearest station within 30 miles. Rain and melted snow; not a measure of sunshine."
        ),
    },
    "annual_snowfall_in": {
        "label": "Annual snowfall",
        "unit": "inches per year",
        "definition": (
            "Annual snowfall normal in inches, NOAA 1991-2020 ANN-SNOW-NORMAL, from the nearest "
            "station within 30 miles that reports snowfall. Missing where none does; never "
            "assumed to be zero."
        ),
    },
}

LSAD_SUFFIXES = (
    " city and borough",
    " census designated place",
    " municipality",
    " consolidated government (balance)",
    " metropolitan government (balance)",
    " unified government (balance)",
    " urban county",
    " (balance)",
    " borough",
    " village",
    " town",
    " city",
    " CDP",
    " comunidad",
    " zona urbana",
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch(url: str, destination: Path) -> bytes:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        print(f"downloading {url}", file=sys.stderr)
        with urllib.request.urlopen(url, timeout=600) as response:
            destination.write_bytes(response.read())
    return destination.read_bytes()


def clean_value(raw: str) -> float | None:
    """Parse an ACS estimate; blanks and negative values are missing."""
    text = raw.strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    return None if value < 0 else value


def read_acs_table(data: bytes) -> dict[str, dict[str, float | None]]:
    """Return {place GEO_ID suffix (state+place FIPS): {estimate variable: value}}."""
    reader = csv.reader(io.StringIO(data.decode("utf-8")), delimiter="|")
    header = next(reader)
    estimates = [(index, name) for index, name in enumerate(header) if "_E" in name]
    rows: dict[str, dict[str, float | None]] = {}
    for record in reader:
        geo_id = record[0]
        if not geo_id.startswith("1600000US"):
            continue
        rows[geo_id.removeprefix("1600000US")] = {
            name: clean_value(record[index]) for index, name in estimates
        }
    return rows


def share(
    row: dict[str, float | None], numerator: tuple[str, ...], denominator: str
) -> float | None:
    total = row.get(denominator)
    parts = [row.get(name) for name in numerator]
    if total is None or total <= 0 or any(part is None for part in parts):
        return None
    value = sum(part for part in parts if part is not None) / total * 100
    return round(min(value, 100.0), 2)


def display_name(raw_name: str) -> str:
    for suffix in LSAD_SUFFIXES:
        if raw_name.endswith(suffix):
            return raw_name[: -len(suffix)]
    return raw_name


def read_gazetteer(data: bytes) -> list[dict[str, str]]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        name = next(entry for entry in archive.namelist() if entry.endswith(".txt"))
        text = archive.read(name).decode("utf-8")
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    reader.fieldnames = [field.strip() for field in reader.fieldnames or []]
    return [{key: value.strip() for key, value in row.items()} for row in reader]


def number(value: float | None, digits: int = 0) -> str:
    if value is None:
        return ""
    return str(round(value)) if digits == 0 else f"{value:.{digits}f}"


Station = tuple[str, str, float, float, dict[str, float]]  # id, name, lat, lon, variables


def read_noaa_stations(data: bytes) -> list[Station]:
    """Read U.S. station annual normals from the NOAA tarball; missing values are omitted."""
    wanted = {variable for group in CLIMATE_GROUPS.values() for _, variable in group}
    stations: list[Station] = []
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for member in archive:
            if not member.isfile() or not member.name.endswith(".csv"):
                continue
            handle = archive.extractfile(member)
            if handle is None:
                continue
            row = next(csv.DictReader(io.TextIOWrapper(handle, encoding="utf-8")), None)
            if row is None or not row["STATION"].startswith("US"):
                continue
            variables: dict[str, float] = {}
            try:
                for variable in wanted:
                    text = (row.get(variable) or "").strip()
                    if text:
                        value = float(text)
                        if value >= 0:  # NOAA marks missing values with -9999
                            variables[variable] = value
                latitude, longitude = float(row["LATITUDE"]), float(row["LONGITUDE"])
            except (KeyError, ValueError) as exc:
                raise SystemExit(f"unreadable NOAA station file {member.name}: {exc!r}") from exc
            stations.append(
                (
                    row["STATION"],
                    row["NAME"].strip(),
                    latitude,
                    longitude,
                    variables,
                )
            )
    return sorted(stations, key=lambda station: station[0])


def miles_between(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in miles."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    a = (
        math.sin((phi2 - phi1) / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    )
    return 3958.7613 * 2 * math.asin(math.sqrt(a))


class StationIndex:
    """Nearest station reporting every variable in a group, found through 1-degree cells."""

    def __init__(self, stations: list[Station], variables: tuple[str, ...]) -> None:
        self.variables = variables
        self.cells: dict[tuple[int, int], list[Station]] = {}
        for station in stations:
            if all(variable in station[4] for variable in variables):
                cell = (math.floor(station[2]), math.floor(station[3]))
                self.cells.setdefault(cell, []).append(station)

    def nearest(self, lat: float, lon: float) -> tuple[Station, float] | None:
        lon_span = min(math.ceil(1.0 / max(math.cos(math.radians(lat)), 0.2)) + 1, 90)
        best: tuple[float, str, Station] | None = None
        for d_lat in range(-2, 3):
            for d_lon in range(-lon_span, lon_span + 1):
                # Longitude cells wrap across the antimeridian (western Aleutian places).
                lon_cell = (math.floor(lon) + d_lon + 180) % 360 - 180
                for station in self.cells.get((math.floor(lat) + d_lat, lon_cell), ()):
                    miles = miles_between(lat, lon, station[2], station[3])
                    if miles <= CLIMATE_RADIUS_MILES and (
                        best is None or (miles, station[0]) < best[:2]
                    ):
                        best = (miles, station[0], station)
        return None if best is None else (best[2], best[0])


def climate_for(
    place: dict[str, str], indexes: dict[str, StationIndex]
) -> tuple[dict[str, str], str]:
    """Return catalog cells and the provenance JSON for one Gazetteer place."""
    cells = {field: "" for group in CLIMATE_GROUPS.values() for field, _ in group}
    sources: dict[str, list[Any]] = {}
    lat, lon = float(place["INTPTLAT"]), float(place["INTPTLONG"])
    for group, fields in CLIMATE_GROUPS.items():
        found = indexes[group].nearest(lat, lon)
        if found is None:
            continue
        station, miles = found
        for field, variable in fields:
            cells[field] = number(station[4][variable], 1)
        sources[group] = [station[0], station[1], round(miles, 1)]
    return cells, json.dumps(sources, sort_keys=True, separators=(",", ":")) if sources else ""


def build_rows(
    gazetteer: list[dict[str, str]],
    tables: dict[str, dict[str, dict[str, float | None]]],
    stations: list[Station],
) -> list[dict[str, str]]:
    indexes = {
        group: StationIndex(stations, tuple(variable for _, variable in fields))
        for group, fields in CLIMATE_GROUPS.items()
    }
    merged: dict[str, dict[str, float | None]] = {}
    for table in tables.values():
        for geoid, values in table.items():
            merged.setdefault(geoid, {}).update(values)

    candidates = [row for row in gazetteer if row["USPS"] in STATE_REGIONS]
    name_counts = Counter((display_name(row["NAME"]), row["USPS"]) for row in candidates)
    rows: list[dict[str, str]] = []
    for place in sorted(candidates, key=lambda row: row["GEOID"]):
        acs = merged.get(place["GEOID"], {})
        name = display_name(place["NAME"])
        kind = place["NAME"].removeprefix(name).strip()
        if name_counts[(name, place["USPS"])] > 1 and kind:
            name = f"{name} ({kind})"
        population = acs.get("B01003_E001")
        land = float(place["ALAND_SQMI"]) if place["ALAND_SQMI"] else 0.0
        density = population / land if population is not None and land > 0 else None
        climate, climate_sources = climate_for(place, indexes)
        rows.append(
            {
                "place_id": place["GEOID"],
                "name": name,
                "state": place["USPS"],
                "region": STATE_REGIONS[place["USPS"]],
                "population": number(population),
                "median_home_value": number(acs.get("B25077_E001")),
                "population_density": number(density, 1),
                "car_light_commute_share": number(
                    share(acs, CAR_LIGHT_VARIABLES, "B08301_E001"), 2
                ),
                "college_educated_share": number(share(acs, COLLEGE_VARIABLES, "B15003_E001"), 2),
                "older_adult_share": number(share(acs, OLDER_ADULT_VARIABLES, "B01001_E001"), 2),
                **climate,
                "land_area_sqmi": place["ALAND_SQMI"],
                "latitude": place["INTPTLAT"],
                "longitude": place["INTPTLONG"],
                "climate_sources": climate_sources,
            }
        )
    return rows


def encode_catalog(rows: list[dict[str, str]]) -> tuple[bytes, bytes]:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=CATALOG_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    raw = buffer.getvalue().encode("utf-8")
    compressed = gzip.compress(raw, compresslevel=9, mtime=0)
    return raw, compressed


def coverage_and_bounds(rows: list[dict[str, str]]) -> tuple[dict[str, Any], dict[str, Any]]:
    serving = [
        row
        for row in rows
        if row["population"] and float(row["population"]) >= MINIMUM_SERVING_POPULATION
    ]
    coverage: dict[str, Any] = {}
    bounds: dict[str, Any] = {}
    for field in SCORED_FIELDS:
        values = sorted(float(row[field]) for row in serving if row[field])
        coverage[field] = {
            "non_null": len(values),
            "serving_places": len(serving),
            "share": round(len(values) / len(serving), 4),
        }
        bounds[field] = {
            "lower": nearest_rank_percentile(values, 0.05),
            "upper": nearest_rank_percentile(values, 0.95),
        }
    return coverage, bounds


def build() -> tuple[bytes, bytes, dict[str, Any]]:
    inputs: dict[str, dict[str, str]] = {}
    gazetteer_bytes = fetch(GAZETTEER_URL, CACHE / "2024_Gaz_place_national.zip")
    inputs["gazetteer"] = {
        "url": GAZETTEER_URL,
        "vintage": GAZETTEER_VINTAGE,
        "sha256": sha256_bytes(gazetteer_bytes),
    }
    tables: dict[str, dict[str, dict[str, float | None]]] = {}
    for table in ACS_TABLES:
        url = f"{ACS_BASE}/data/5YRData/acsdt5y2024-{table}.dat"
        data = fetch(url, CACHE / f"acsdt5y2024-{table}.dat")
        inputs[table] = {"url": url, "vintage": ACS_VINTAGE, "sha256": sha256_bytes(data)}
        tables[table] = read_acs_table(data)

    noaa_bytes = fetch(NOAA_URL, CACHE / "noaa-normals-1991-2020-annualseasonal.tar.gz")
    inputs["noaa_normals"] = {
        "url": NOAA_URL,
        "vintage": NOAA_VINTAGE,
        "sha256": sha256_bytes(noaa_bytes),
    }
    rows = build_rows(read_gazetteer(gazetteer_bytes), tables, read_noaa_stations(noaa_bytes))
    raw, compressed = encode_catalog(rows)
    coverage, bounds = coverage_and_bounds(rows)
    failing = {field: c["share"] for field, c in coverage.items() if c["share"] < 0.8}
    if failing:
        raise SystemExit(f"fields below the 80% FR4 coverage floor: {failing}")
    manifest: dict[str, Any] = {
        "catalog_version": CATALOG_VERSION,
        "normalization_version": NORMALIZATION_VERSION,
        "data_date": DATA_DATE,
        "inputs": inputs,
        "geography": "U.S. states and DC: incorporated places and Census-designated places",
        "excluded_geography": "Puerto Rico places are not in the first catalog.",
        "climate_join": (
            f"Each place uses the nearest NOAA station within {CLIMATE_RADIUS_MILES:.0f} miles "
            "that reports the needed normals, measured from the Census place centroid. Station "
            "elevation is not compared; values are left missing when no station qualifies."
        ),
        "sentinel_policy": SENTINEL_POLICY,
        "serving_universe": f"places with known population >= {MINIMUM_SERVING_POPULATION}",
        "fields": FIELD_DEFINITIONS,
        "coverage": coverage,
        "bounds": bounds,
        "row_count": len(rows),
        "output_file": CATALOG_PATH.name,
        "output_sha256": sha256_bytes(compressed),
        "uncompressed_sha256": sha256_bytes(raw),
    }
    return raw, compressed, manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true", help="fail if a rebuild differs")
    args = parser.parse_args()
    _, compressed, manifest = build()
    text = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    if args.verify:
        shipped = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        if (
            shipped["uncompressed_sha256"] != manifest["uncompressed_sha256"]
            or shipped["inputs"] != manifest["inputs"]
        ):
            print("catalog rebuild does not match the shipped manifest", file=sys.stderr)
            return 1
        print("catalog rebuild matches the shipped manifest")
        return 0
    CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CATALOG_PATH.write_bytes(compressed)
    MANIFEST_PATH.write_text(text, encoding="utf-8")
    print(f"wrote {manifest['row_count']} places; {CATALOG_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
