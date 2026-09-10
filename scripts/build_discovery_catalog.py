#!/usr/bin/env python3
"""Build the checked-in place-discovery catalog from Census bulk files."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import zipfile
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import urlopen

from lifescape.discovery import (
    SUPPORTED_DIMENSIONS,
    CatalogPlace,
    DiscoveryCatalog,
    serialize_catalog,
)

GAZETTEER_URL = (
    "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2024_Gazetteer/"
    "2024_Gaz_place_national.zip"
)
ACS_ROOT = (
    "https://www2.census.gov/programs-surveys/acs/summary_file/2023/table-based-SF/data/5YRData"
)
ACS_FILES = {
    "population": "acsdt5y2023-b01003.dat",
    "age": "acsdt5y2023-b01001.dat",
    "education": "acsdt5y2023-b15003.dat",
    "commute": "acsdt5y2023-b08301.dat",
    "housing": "acsdt5y2023-b25077.dat",
}
SOURCE_URLS = (GAZETTEER_URL, *(f"{ACS_ROOT}/{name}" for name in ACS_FILES.values()))


def _download(url: str) -> bytes:
    with urlopen(url, timeout=120) as response:
        return response.read()


def _table(raw: bytes) -> dict[str, dict[str, str]]:
    lines = raw.decode("utf-8-sig").splitlines()
    headers = lines[0].split("|")
    return {
        row[0]: dict(zip(headers[1:], row[1:], strict=True))
        for row in (line.split("|") for line in lines[1:] if line)
        if row and row[0].startswith("1600000US")
    }


def _number(row: dict[str, str], key: str) -> float | None:
    raw = row.get(key, "").strip()
    if not raw:
        return None
    value = float(raw)
    return None if value < 0 else value


def _share(
    row: dict[str, str], numerator_keys: tuple[str, ...], denominator_key: str
) -> float | None:
    denominator = _number(row, denominator_key)
    values = [_number(row, key) for key in numerator_keys]
    if denominator in (None, 0) or any(value is None for value in values):
        return None
    return sum(value for value in values if value is not None) / denominator * 100


def _car_light_share(row: dict[str, str]) -> float | None:
    total = _number(row, "B08301_E001")
    drove_alone = _number(row, "B08301_E003")
    if total in (None, 0) or drove_alone is None:
        return None
    return (total - drove_alone) / total * 100


def _read_gazetteer(raw: bytes) -> dict[str, dict[str, str]]:
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        names = [name for name in archive.namelist() if name.endswith("_Gaz_place_national.txt")]
        if len(names) != 1:
            raise ValueError("Census Gazetteer archive does not contain one national place file")
        lines = archive.read(names[0]).decode("utf-8-sig").splitlines()
    headers = lines[0].split("\t")
    return {
        row[1]: dict(zip(headers, (item.strip() for item in row), strict=True))
        for row in (line.split("\t") for line in lines[1:] if line)
    }


def build_catalog() -> tuple[tuple[CatalogPlace, ...], dict[str, object]]:
    with TemporaryDirectory(prefix="lifescape-catalog-") as temporary:
        root = Path(temporary)
        gazetteer_raw = _download(GAZETTEER_URL)
        gazetteer = _read_gazetteer(gazetteer_raw)
        tables: dict[str, dict[str, dict[str, str]]] = {}
        acs_hashes: dict[str, str] = {}
        for name, filename in ACS_FILES.items():
            raw = _download(f"{ACS_ROOT}/{filename}")
            (root / filename).write_bytes(raw)
            tables[name] = _table(raw)
            acs_hashes[filename] = hashlib.sha256(raw).hexdigest()

        places: list[CatalogPlace] = []
        for geoid, gazetteer_row in sorted(gazetteer.items()):
            geo_key = f"1600000US{geoid}"
            population_row = tables["population"].get(geo_key)
            age_row = tables["age"].get(geo_key)
            education_row = tables["education"].get(geo_key)
            commute_row = tables["commute"].get(geo_key)
            housing_row = tables["housing"].get(geo_key)
            if population_row is None:
                continue
            population = _number(population_row, "B01003_E001")
            area = float(gazetteer_row["ALAND_SQMI"])
            values = {
                "population": population,
                "housing_cost": _number(housing_row or {}, "B25077_E001"),
                "population_density": (
                    population / area if population is not None and area > 0 else None
                ),
                "car_light_commute_share": _car_light_share(commute_row or {}),
                "college_educated_share": _share(
                    education_row or {},
                    ("B15003_E022", "B15003_E023", "B15003_E024", "B15003_E025"),
                    "B15003_E001",
                ),
                "older_adult_share": _share(
                    age_row or {},
                    tuple(f"B01001_E{index:03d}" for index in (*range(20, 26), *range(43, 49))),
                    "B01001_E001",
                ),
            }
            places.append(
                CatalogPlace(
                    place_id=f"us-place-{geoid}",
                    name=gazetteer_row["NAME"],
                    state=gazetteer_row["USPS"],
                    values=values,
                )
            )
        catalog = DiscoveryCatalog.from_places(
            tuple(places),
            catalog_version="census-acs5-2023-gazetteer-2024",
            data_date=date(2023, 12, 31),
            source_urls=SOURCE_URLS,
            source_vintage="ACS 2023 5-year table-based summary files; Census Gazetteer 2024",
        )
        coverage = catalog.manifest.coverage
        if any(coverage[dimension] < 0.8 for dimension in SUPPORTED_DIMENSIONS):
            raise ValueError(f"catalog coverage is below 80 percent: {coverage}")
        manifest = catalog.manifest.model_dump(mode="json")
        manifest["source_hashes"] = {
            "gazetteer": {"2024_Gaz_place_national.zip": hashlib.sha256(gazetteer_raw).hexdigest()},
            "acs": acs_hashes,
        }
        manifest["selected_fields"] = {
            "population": "B01003_E001",
            "housing_cost": "B25077_E001",
            "population_density": "population / ALAND_SQMI",
            "car_light_commute_share": "(B08301_E001 - B08301_E003) / B08301_E001 * 100",
            "college_educated_share": "sum(B15003_E022..B15003_E025) / B15003_E001 * 100",
            "older_adult_share": "sum(B01001_E020..E025,E043..E048) / B01001_E001 * 100",
        }
        manifest["missing_value_policy"] = "blank or negative ACS estimates become null"
        return catalog.places, manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path("data/discovery"))
    args = parser.parse_args()
    places, manifest = build_catalog()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "catalog.json").write_bytes(serialize_catalog(places))
    (args.output_dir / "catalog-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(places)} places to {args.output_dir}")


if __name__ == "__main__":
    main()
