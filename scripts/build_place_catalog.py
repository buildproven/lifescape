"""Build the packaged place-discovery catalog from official Census bulk files.

Inputs (all keyless, public, pinned by SHA-256 in the manifest):
- 2024 Census Gazetteer, places: identity, land area, centroid.
- NOAA U.S. Climate Normals 1991-2020 (annual/seasonal, by station): freezing nights, 90°F+ days,
  precipitation, snowfall, joined to each place by the nearest reporting station within 30 miles.
- ACS 2020-2024 5-year table-based summary files: B01003, B25077, B08301, B15003, B01001.
- Natural Earth 1:10m ocean coastline and Great Lakes outlines: distance to nearest shore.
- USGS GNIS populated-place elevations (2021-08-25 archive): elevation above sea level.
- 2024 Census Gazetteer core-based statistical areas plus ACS B01003 metro population: distance to
  the principal cities of metros over 1 million people, and a suburb flag.

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
import re
import sys
import tarfile
import urllib.request
import zipfile
from collections import Counter
from itertools import pairwise
from pathlib import Path
from typing import Any, NamedTuple

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
GAZETTEER_CBSA_URL = (
    "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2024_Gazetteer/"
    "2024_Gaz_cbsa_national.zip"
)
NATURAL_EARTH_BASE = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2/geojson"
COASTLINE_URL = f"{NATURAL_EARTH_BASE}/ne_10m_coastline.geojson"
LAKES_URL = f"{NATURAL_EARTH_BASE}/ne_10m_lakes.geojson"
GNIS_URL = (
    "https://prd-tnm.s3.amazonaws.com/StagedProducts/GeographicNames/Archive/MainDomestic/"
    "AllStates.zip"
)
GNIS_VINTAGE = "USGS GNIS Domestic Names, 2021-08-25 archive"
NATURAL_EARTH_VINTAGE = "Natural Earth 1:10m physical vectors v5.1.2 (public domain)"
GREAT_LAKES = ("Lake Superior", "Lake Michigan", "Lake Huron", "Lake Erie", "Lake Ontario")
LARGE_METRO_POPULATION = 1_000_000
METRO_SUBURB_RADIUS_MILES = 25.0
ELEVATION_RADIUS_MILES = 5.0
EXACT_SHORE_MILES = 60.0  # nearer than this, distance is measured to the shoreline segments
FAR_SHORE_SPACING_MILES = 4.0  # farther, to shoreline vertices thinned to this spacing
SHORE_CELL_DEGREES = 0.5
CATALOG_VERSION = "us-places-acs2024-noaa1991-2020-character-v3"
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
    "elevation_ft": {
        "label": "Elevation",
        "unit": "feet above sea level",
        "definition": (
            "Elevation of the USGS GNIS populated place with the same name in the same state "
            "nearest the Census place centroid, within 5 miles (GNIS ELEV_IN_FT, 2021-08-25 "
            "archive). Missing where no such place reports elevation."
        ),
    },
    "water_distance_mi": {
        "label": "Distance to ocean or Great Lake",
        "unit": "miles",
        "definition": (
            "Great-circle miles from the Census place centroid to the nearest ocean coastline or "
            "Great Lakes shore (Natural Earth 1:10m). Inland lakes and rivers are not counted. "
            "Measured to shoreline segments within 60 miles and to shoreline points under 4 "
            "miles apart beyond that."
        ),
    },
    "large_metro_distance_mi": {
        "label": "Distance to a large metro",
        "unit": "miles",
        "definition": (
            "Great-circle miles from the Census place centroid to the nearest principal city of "
            "a metropolitan area with 1,000,000 or more people (ACS B01003 for the Census CBSA). "
            "Principal cities are the places named in the metro title; 0 for those cities."
        ),
    },
    "large_metro_suburb": {
        "label": "Large-metro suburb",
        "unit": "yes or no",
        "definition": (
            "1 when the place is not itself a principal city of a metropolitan area over "
            "1,000,000 people and lies within 25 miles of one; otherwise 0. A distance rule, "
            "not a Census urban-area designation."
        ),
    },
}

CITY_STATUS_WORDS = re.compile(
    r" (city|town|village|borough|municipality|(metro|metropolitan|consolidated|unified) "
    r"government)( \(balance\))?$"
)
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


def planar_miles(lat: float, lon: float, other_lat: float, other_lon: float) -> tuple[float, float]:
    """East and north offsets in miles from a point, accurate within a few hundred miles."""
    delta_lon = (other_lon - lon + 180) % 360 - 180
    return (
        math.radians(delta_lon) * math.cos(math.radians(lat)) * 3958.7613,
        math.radians(other_lat - lat) * 3958.7613,
    )


def unit_vector(lat: float, lon: float) -> tuple[float, float, float]:
    phi, lam = math.radians(lat), math.radians(lon)
    return (math.cos(phi) * math.cos(lam), math.cos(phi) * math.sin(lam), math.sin(phi))


def vector_miles(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    dot = a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
    return 3958.7613 * math.acos(min(1.0, max(-1.0, dot)))


Shore = list[list[tuple[float, float]]]  # polylines of (latitude, longitude)


def read_shorelines(coastline: bytes, lakes: bytes) -> Shore:
    """Ocean coastline plus the five Great Lakes outlines, kept where they can serve U.S. places."""

    def in_range(lat: float, lon: float) -> bool:
        return 15.0 <= lat <= 75.0 and (lon <= -50.0 or lon >= 160.0)

    def clip(line: list[list[float]]) -> Shore:
        pieces: Shore = []
        current: list[tuple[float, float]] = []
        for lon, lat in line:
            if in_range(lat, lon):
                current.append((lat, lon))
            elif current:
                pieces.append(current)
                current = []
        if current:
            pieces.append(current)
        return [piece for piece in pieces if len(piece) > 1]

    lines: Shore = []
    for feature in json.loads(coastline)["features"]:
        geometry = feature["geometry"]
        parts = (
            [geometry["coordinates"]]
            if geometry["type"] == "LineString"
            else geometry["coordinates"]
        )
        for part in parts:
            lines.extend(clip(part))
    found: set[str] = set()
    for feature in json.loads(lakes)["features"]:
        name = feature["properties"]["name"]
        if name not in GREAT_LAKES:
            continue
        found.add(name)
        geometry = feature["geometry"]
        polygons = (
            [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
        )
        for polygon in polygons:
            for ring in polygon:
                lines.extend(clip(ring))
    if found != set(GREAT_LAKES):
        raise SystemExit(f"Natural Earth lakes are missing {sorted(set(GREAT_LAKES) - found)}")
    return lines


class ShoreIndex:
    """Nearest-shore miles for many places, sharing work between places in one grid cell."""

    def __init__(self, lines: Shore) -> None:
        self.segments: list[tuple[float, float, float, float]] = []
        self.thin: list[tuple[float, float, float]] = []
        self.grid: dict[tuple[int, int], list[int]] = {}
        for line in lines:
            kept = line[0]
            self.thin.append(unit_vector(*kept))
            for start, end in pairwise(line):
                segment = len(self.segments)
                self.segments.append((*start, *end))
                for cell in self._cells(start, end):
                    self.grid.setdefault(cell, []).append(segment)
                steps = math.ceil(miles_between(*start, *end) / FAR_SHORE_SPACING_MILES)
                for step in range(1, steps + 1):
                    lat = start[0] + (end[0] - start[0]) * step / steps
                    lon = start[1] + (end[1] - start[1]) * step / steps
                    if miles_between(*kept, lat, lon) >= FAR_SHORE_SPACING_MILES:
                        kept = (lat, lon)
                        self.thin.append(unit_vector(lat, lon))

    @staticmethod
    def _cells(start: tuple[float, float], end: tuple[float, float]) -> set[tuple[int, int]]:
        lat_cells = range(math.floor(min(start[0], end[0])), math.floor(max(start[0], end[0])) + 1)
        lon_low, lon_high = sorted((start[1], end[1]))
        lon_cells = range(math.floor(lon_low), math.floor(lon_high) + 1)
        return {(lat, lon) for lat in lat_cells for lon in lon_cells}

    def _segments_near(self, lat: float, lon: float, radius: float) -> set[int]:
        lat_span = math.ceil(radius / 69.0) + 1
        lon_span = min(math.ceil(radius / (69.0 * max(math.cos(math.radians(lat)), 0.2))) + 1, 90)
        found: set[int] = set()
        for d_lat in range(-lat_span, lat_span + 1):
            for d_lon in range(-lon_span, lon_span + 1):
                lon_cell = (math.floor(lon) + d_lon + 180) % 360 - 180
                found.update(self.grid.get((math.floor(lat) + d_lat, lon_cell), ()))
        return found

    def distances(self, places: list[tuple[str, float, float]]) -> dict[str, float]:
        """Return ``{place id: miles to nearest shore}`` for ``(id, lat, lon)`` places."""
        cells: dict[tuple[int, int], list[tuple[str, float, float]]] = {}
        for place in places:
            key = (
                math.floor(place[1] / SHORE_CELL_DEGREES),
                math.floor(place[2] / SHORE_CELL_DEGREES),
            )
            cells.setdefault(key, []).append(place)
        result: dict[str, float] = {}
        for (lat_cell, lon_cell), members in sorted(cells.items()):
            center_lat = (lat_cell + 0.5) * SHORE_CELL_DEGREES
            center_lon = (lon_cell + 0.5) * SHORE_CELL_DEGREES
            center = unit_vector(center_lat, center_lon)
            half_diagonal = miles_between(
                center_lat,
                center_lon,
                center_lat + SHORE_CELL_DEGREES / 2,
                center_lon + SHORE_CELL_DEGREES / 2,
            )
            cx, cy, cz = center
            dots = [cx * x + cy * y + cz * z for x, y, z in self.thin]
            nearest = 3958.7613 * math.acos(min(1.0, max(dots)))
            reach = nearest + 2 * half_diagonal
            if nearest <= EXACT_SHORE_MILES:
                segments = [
                    self.segments[i]
                    for i in sorted(self._segments_near(center_lat, center_lon, reach))
                ]
                for place_id, lat, lon in members:
                    result[place_id] = min(segment_miles(lat, lon, *seg) for seg in segments)
            else:
                floor = math.cos(min(reach / 3958.7613, math.pi))
                nearby = [v for v, dot in zip(self.thin, dots, strict=True) if dot >= floor]
                for place_id, lat, lon in members:
                    here = unit_vector(lat, lon)
                    result[place_id] = min(vector_miles(here, vector) for vector in nearby)
        return result


def segment_miles(
    lat: float, lon: float, lat1: float, lon1: float, lat2: float, lon2: float
) -> float:
    """Miles from a point to a short shoreline segment, in a local flat projection."""
    x1, y1 = planar_miles(lat, lon, lat1, lon1)
    x2, y2 = planar_miles(lat, lon, lat2, lon2)
    dx, dy = x2 - x1, y2 - y1
    length_squared = dx * dx + dy * dy
    t = 0.0 if length_squared == 0 else max(0.0, min(1.0, -(x1 * dx + y1 * dy) / length_squared))
    return math.hypot(x1 + t * dx, y1 + t * dy)


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


CHARACTER_FIELDS = (
    "elevation_ft",
    "water_distance_mi",
    "large_metro_distance_mi",
    "large_metro_suburb",
)
Elevations = dict[tuple[str, str], list[tuple[float, float, float]]]  # (state, name) -> points
Metro = tuple[str, list[str], list[str]]  # CBSA title, principal city names, state codes


class Geography(NamedTuple):
    """Place-character inputs: shoreline, GNIS elevations, and large-metro definitions."""

    shore: ShoreIndex
    elevations: Elevations
    metros: list[Metro]


def match_name(name: str) -> str:
    """Fold a place name for matching: drop a trailing qualifier, punctuation, and Saint/St."""
    base = name.split(" (")[0].casefold().replace("saint ", "st ").replace("st. ", "st ")
    return "".join(character for character in base if character.isalnum())


def read_gnis_elevations(data: bytes) -> Elevations:
    """Read GNIS populated places that report an elevation, grouped by state."""
    found: Elevations = {}
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for entry in sorted(archive.namelist()):
            if not entry.endswith("_Features_20210825.txt"):
                continue
            text = archive.read(entry).decode("utf-8-sig")
            for row in csv.DictReader(io.StringIO(text), delimiter="|"):
                if row["FEATURE_CLASS"] != "Populated Place" or not row["ELEV_IN_FT"].strip():
                    continue
                try:
                    point = (
                        float(row["PRIM_LAT_DEC"]),
                        float(row["PRIM_LONG_DEC"]),
                        float(row["ELEV_IN_FT"]),
                    )
                except ValueError as exc:
                    raise SystemExit(f"unreadable GNIS row in {entry}: {exc!r}") from exc
                found.setdefault((row["STATE_ALPHA"], match_name(row["FEATURE_NAME"])), []).append(
                    point
                )
    return found


def elevation_for(name: str, state: str, lat: float, lon: float, elevations: Elevations) -> str:
    """Feet for the nearest same-named GNIS populated place within 5 miles; blank otherwise."""
    best: tuple[float, float] | None = None
    for c_lat, c_lon, feet in elevations.get((state, match_name(name)), ()):
        miles = miles_between(lat, lon, c_lat, c_lon)
        if miles <= ELEVATION_RADIUS_MILES and (best is None or (miles, feet) < best):
            best = (miles, feet)
    return "" if best is None else number(best[1])


def read_large_metros(cbsa_gazetteer: bytes, b01003: bytes) -> list[Metro]:
    """Metropolitan areas with 1,000,000+ people, with the principal cities their titles name."""
    populations: dict[str, float] = {}
    for line in b01003.decode("utf-8").splitlines()[1:]:
        geo_id, estimate, *_ = line.split("|")
        if geo_id.startswith("310M700US") and estimate.lstrip("-").isdigit():
            populations[geo_id.removeprefix("310M700US")] = float(estimate)
    with zipfile.ZipFile(io.BytesIO(cbsa_gazetteer)) as archive:
        name = next(entry for entry in archive.namelist() if entry.endswith(".txt"))
        text = archive.read(name).decode("utf-8")
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    reader.fieldnames = [field.strip() for field in reader.fieldnames or []]
    metros: list[Metro] = []
    for row in reader:
        title = row["NAME"].strip()
        if (
            row["CBSA_TYPE"].strip() != "1"
            or populations.get(row["GEOID"].strip(), 0.0) < LARGE_METRO_POPULATION
        ):
            continue
        head, _, states = title.removesuffix(" Metro Area").rpartition(", ")
        cities = head.split("--") if "--" in head else head.split("-")
        if any(state in STATE_REGIONS for state in states.split("-")):  # not Puerto Rico
            metros.append((title, cities, states.split("-")))
    return metros


def metro_cores(
    metros: list[Metro], places: list[dict[str, Any]]
) -> tuple[dict[str, tuple[float, float]], list[str]]:
    """Map each metro's principal cities to the largest same-named place in its states.

    Returns ``({place id: (lat, lon)}, [metro titles whose first city matched no place])``.
    """
    by_name: dict[str, list[dict[str, Any]]] = {}
    for place in places:
        # "Indianapolis city (balance)" keeps its legal-status words after display_name().
        plain = CITY_STATUS_WORDS.sub("", place["name"])
        for name in {match_name(place["name"]), match_name(plain)}:
            by_name.setdefault(name, []).append(place)
    cores: dict[str, tuple[float, float]] = {}
    unmatched: list[str] = []
    for title, cities, states in metros:
        matched = False
        for city in cities:
            options = [p for p in by_name.get(match_name(city), ()) if p["state"] in states]
            if options:
                best = max(options, key=lambda p: (p["population"] or 0.0, p["id"]))
                cores[best["id"]] = (best["lat"], best["lon"])
                matched = matched or city == cities[0]
        if not matched:
            unmatched.append(title)
    return cores, unmatched


def character_cells(
    places: list[dict[str, Any]], geography: Geography
) -> dict[str, dict[str, str]]:
    """Return the place-character catalog cells keyed by place id; unknown stays blank."""
    cores, unmatched = metro_cores(geography.metros, places)
    if unmatched:
        raise SystemExit(f"no Gazetteer place matches the first city of metros: {unmatched}")
    shore = geography.shore.distances([(p["id"], p["lat"], p["lon"]) for p in places])
    cells: dict[str, dict[str, str]] = {}
    for place in places:
        core_miles = min(
            (
                0.0 if core_id == place["id"] else miles_between(place["lat"], place["lon"], *where)
                for core_id, where in cores.items()
            ),
            default=None,
        )
        cells[place["id"]] = {
            "elevation_ft": elevation_for(
                place["name"], place["state"], place["lat"], place["lon"], geography.elevations
            ),
            "water_distance_mi": number(shore[place["id"]], 1),
            "large_metro_distance_mi": number(core_miles, 1),
            "large_metro_suburb": (
                ""
                if core_miles is None
                else "1"
                if 0 < core_miles <= METRO_SUBURB_RADIUS_MILES
                else "0"
            ),
        }
    return cells


def build_rows(
    gazetteer: list[dict[str, str]],
    tables: dict[str, dict[str, dict[str, float | None]]],
    stations: list[Station],
    geography: Geography | None = None,
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
    candidates.sort(key=lambda row: row["GEOID"])
    character = (
        {}
        if geography is None
        else character_cells(
            [
                {
                    "id": row["GEOID"],
                    "name": display_name(row["NAME"]),
                    "state": row["USPS"],
                    "lat": float(row["INTPTLAT"]),
                    "lon": float(row["INTPTLONG"]),
                    "population": merged.get(row["GEOID"], {}).get("B01003_E001"),
                }
                for row in candidates
            ],
            geography,
        )
    )
    rows: list[dict[str, str]] = []
    for place in candidates:
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
                **character.get(place["GEOID"], dict.fromkeys(CHARACTER_FIELDS, "")),
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
    metro_population = b""
    for table in ACS_TABLES:
        url = f"{ACS_BASE}/data/5YRData/acsdt5y2024-{table}.dat"
        data = fetch(url, CACHE / f"acsdt5y2024-{table}.dat")
        if table == "b01003":
            metro_population = data
        inputs[table] = {"url": url, "vintage": ACS_VINTAGE, "sha256": sha256_bytes(data)}
        tables[table] = read_acs_table(data)

    noaa_bytes = fetch(NOAA_URL, CACHE / "noaa-normals-1991-2020-annualseasonal.tar.gz")
    inputs["noaa_normals"] = {
        "url": NOAA_URL,
        "vintage": NOAA_VINTAGE,
        "sha256": sha256_bytes(noaa_bytes),
    }
    sources = {
        "cbsa_gazetteer": (GAZETTEER_CBSA_URL, GAZETTEER_VINTAGE, "2024_Gaz_cbsa_national.zip"),
        "natural_earth_coastline": (
            COASTLINE_URL,
            NATURAL_EARTH_VINTAGE,
            "ne_10m_coastline.geojson",
        ),
        "natural_earth_lakes": (LAKES_URL, NATURAL_EARTH_VINTAGE, "ne_10m_lakes.geojson"),
        "gnis_populated_places": (GNIS_URL, GNIS_VINTAGE, "gnis-AllStates-20210825.zip"),
    }
    downloaded: dict[str, bytes] = {}
    for key, (url, vintage, cache_name) in sources.items():
        downloaded[key] = fetch(url, CACHE / cache_name)
        inputs[key] = {"url": url, "vintage": vintage, "sha256": sha256_bytes(downloaded[key])}
    geography = Geography(
        ShoreIndex(
            read_shorelines(
                downloaded["natural_earth_coastline"], downloaded["natural_earth_lakes"]
            )
        ),
        read_gnis_elevations(downloaded["gnis_populated_places"]),
        read_large_metros(downloaded["cbsa_gazetteer"], metro_population),
    )
    rows = build_rows(
        read_gazetteer(gazetteer_bytes), tables, read_noaa_stations(noaa_bytes), geography
    )
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
        "character_join": (
            "Elevation, shoreline distance, and large-metro distance are measured from the "
            "Census place centroid. Elevation is the GNIS populated place of the same name in "
            f"the same state within {ELEVATION_RADIUS_MILES:.0f} miles; it is left missing when "
            "none reports one. Large metros are Census metropolitan areas with "
            f"{LARGE_METRO_POPULATION:,}+ people (ACS B01003); their principal cities are the "
            "places named in the metro title."
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
