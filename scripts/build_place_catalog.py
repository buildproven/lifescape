"""Build the packaged place-discovery catalog from official Census bulk files.

Inputs (all keyless, public, pinned by SHA-256 in the manifest):
- 2024 Census Gazetteer, places: identity, land area, centroid.
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
import sys
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
CATALOG_VERSION = "us-places-acs2024-v1"
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


def build_rows(
    gazetteer: list[dict[str, str]], tables: dict[str, dict[str, dict[str, float | None]]]
) -> list[dict[str, str]]:
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
                "land_area_sqmi": place["ALAND_SQMI"],
                "latitude": place["INTPTLAT"],
                "longitude": place["INTPTLONG"],
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

    rows = build_rows(read_gazetteer(gazetteer_bytes), tables)
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
