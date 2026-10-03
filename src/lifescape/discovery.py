"""Deterministic place discovery over a versioned, packaged U.S. place catalog.

Contract: docs/decisions/ADR-place-discovery-contract.md. PRD: FR2-FR9, FR11.

Discovery data is advisory. This module imports nothing from the evidence, gate, scoring, or
pipeline modules, and its records are never ``ObservationRecord`` values (PRD FR14, G5).
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import math
import re
import unicodedata
from dataclasses import dataclass
from importlib import resources
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ALGORITHM_VERSION: Final = "place-discovery-v1"
NORMALIZATION_VERSION: Final = "discovery-winsorized-minmax-v1"
DEFAULT_RECOMMENDATION_LIMIT: Final = 10
MINIMUM_SERVING_POPULATION: Final = 2500
MINIMUM_MATCH_COMPONENTS: Final = 2
DEFAULT_WEIGHT: Final = 3
REASON_SIMILARITY_FLOOR: Final = 0.75

ScoredField = Literal[
    "population",
    "median_home_value",
    "population_density",
    "car_light_commute_share",
    "college_educated_share",
    "older_adult_share",
]

SCORED_FIELDS: Final[tuple[ScoredField, ...]] = (
    "population",
    "median_home_value",
    "population_density",
    "car_light_commute_share",
    "college_educated_share",
    "older_adult_share",
)
CATALOG_COLUMNS: Final[tuple[str, ...]] = (
    "place_id",
    "name",
    "state",
    "region",
    *SCORED_FIELDS,
    "land_area_sqmi",
    "latitude",
    "longitude",
)
REGIONS: Final = ("Northeast", "Midwest", "South", "West")

_REGION_STATES: Final[dict[str, str]] = {
    "Northeast": "CT ME MA NH RI VT NJ NY PA",
    "Midwest": "IL IN MI OH WI IA KS MN MO NE ND SD",
    "South": "DE DC FL GA MD NC SC VA WV AL KY MS TN AR LA OK TX",
    "West": "AZ CO ID MT NV NM UT WY AK CA HI OR WA",
}
STATE_REGIONS: Final[dict[str, str]] = {
    state: region for region, states in _REGION_STATES.items() for state in states.split()
}


class DiscoveryError(ValueError):
    """Base class for discovery failures that map to a user-correctable 422."""


class CatalogUnavailableError(RuntimeError):
    """The packaged catalog failed integrity verification or could not be loaded."""


class ProfileError(DiscoveryError):
    """The search profile cannot produce a valid discovery search."""


def nearest_rank_percentile(sorted_values: list[float], percentile: float) -> float:
    """Nearest-rank percentile: index ``ceil(p * n) - 1`` bounded to the first and last index."""
    if not sorted_values:
        raise ValueError("percentile requires at least one value")
    index = math.ceil(percentile * len(sorted_values)) - 1
    return sorted_values[min(max(index, 0), len(sorted_values) - 1)]


class DiscoveryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class HardConstraint(DiscoveryModel):
    """A numeric bound that excludes a place only when a known value fails it."""

    field: ScoredField
    operator: Literal["min", "max"]
    value: float = Field(allow_inf_nan=False, ge=0)

    @property
    def constraint_id(self) -> str:
        return f"{self.field}_{self.operator}"


class SearchProfile(DiscoveryModel):
    """What the household likes (exemplars, targets), requires (constraints), and weights."""

    exemplars: tuple[str, ...] = Field(default=(), max_length=2)
    exclude_places: tuple[str, ...] = Field(default=(), max_length=200)
    targets: dict[ScoredField, float] = Field(default_factory=dict)
    priorities: dict[ScoredField, int] = Field(default_factory=dict)
    hard_constraints: tuple[HardConstraint, ...] = Field(default=(), max_length=12)
    include_states: tuple[str, ...] = Field(default=(), max_length=51)
    exclude_states: tuple[str, ...] = Field(default=(), max_length=51)
    include_regions: tuple[str, ...] = Field(default=(), max_length=4)
    exclude_regions: tuple[str, ...] = Field(default=(), max_length=4)

    @field_validator("targets")
    @classmethod
    def targets_are_finite_and_nonnegative(
        cls, value: dict[ScoredField, float]
    ) -> dict[ScoredField, float]:
        for field, target in value.items():
            if not math.isfinite(target) or target < 0:
                raise ValueError(f"target for {field} must be a nonnegative number")
        return value

    @field_validator("priorities")
    @classmethod
    def priorities_are_one_to_five(cls, value: dict[ScoredField, int]) -> dict[ScoredField, int]:
        for field, weight in value.items():
            if not 1 <= weight <= 5:
                raise ValueError(f"priority for {field} must be an integer from 1 to 5")
        return value

    @field_validator("exemplars", "exclude_places")
    @classmethod
    def places_are_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("towns must be distinct")
        return value

    @field_validator("include_states", "exclude_states")
    @classmethod
    def states_are_known(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        unknown = sorted(set(value) - set(STATE_REGIONS))
        if unknown:
            raise ValueError(f"unknown state codes: {', '.join(unknown)}")
        return tuple(sorted(set(value)))

    @field_validator("include_regions", "exclude_regions")
    @classmethod
    def regions_are_known(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        unknown = sorted(set(value) - set(REGIONS))
        if unknown:
            raise ValueError(f"unknown regions: {', '.join(unknown)}")
        return tuple(sorted(set(value)))

    @model_validator(mode="after")
    def constraints_are_distinct(self) -> SearchProfile:
        ids = [constraint.constraint_id for constraint in self.hard_constraints]
        if len(set(ids)) != len(ids):
            raise ValueError("each hard constraint may be set once per field and operator")
        if set(self.exemplars) & set(self.exclude_places):
            raise ValueError("an example town cannot also be excluded")
        if set(self.include_states) & set(self.exclude_states):
            raise ValueError("a state cannot be both included and excluded")
        if set(self.include_regions) & set(self.exclude_regions):
            raise ValueError("a region cannot be both included and excluded")
        return self


@dataclass(frozen=True)
class Place:
    place_id: str
    name: str
    state: str
    region: str
    values: dict[str, float | None]

    @property
    def population(self) -> float | None:
        return self.values["population"]

    @property
    def serving_eligible(self) -> bool:
        population = self.population
        return population is not None and population >= MINIMUM_SERVING_POPULATION

    @property
    def label(self) -> str:
        return f"{self.name}, {self.state}"


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", stripped).strip().casefold()


class PlaceCatalog:
    """Verified, in-memory discovery catalog. Loaded once; never partly loaded."""

    def __init__(self, places: list[Place], manifest: dict[str, Any]) -> None:
        self.places = {place.place_id: place for place in places}
        self.manifest = manifest
        self.catalog_version: str = manifest["catalog_version"]
        self.data_date: str = manifest["data_date"]
        self.fields: dict[str, dict[str, str]] = manifest["fields"]
        self.bounds: dict[str, dict[str, float]] = manifest["bounds"]
        self._serving = sorted(
            (place for place in places if place.serving_eligible), key=lambda p: p.place_id
        )
        self._lookup_index = [(_fold(place.name), _fold(place.label), place) for place in places]

    @property
    def serving_places(self) -> list[Place]:
        return list(self._serving)

    def lookup(self, query: str, limit: int = 10) -> list[Place]:
        """Match by place name or ``Name, ST``: exact, then prefix, then substring."""
        needle = _fold(query)
        name_part, _, state_part = needle.partition(",")
        name_part, state_part = name_part.strip(), state_part.strip()
        ranked: list[tuple[int, float, str, Place]] = []
        for name, label, place in self._lookup_index:
            if state_part and place.state.casefold() != state_part:
                continue
            if name == name_part or label == needle:
                rank = 0
            elif name.startswith(name_part):
                rank = 1
            elif name_part in name:
                rank = 2
            else:
                continue
            ranked.append((rank, -(place.population or 0), place.place_id, place))
        ranked.sort(key=lambda item: item[:3])
        return [item[3] for item in ranked[:limit]]


def load_catalog() -> PlaceCatalog:
    """Verify the packaged catalog against its manifest, then load it.

    Any hash, row-count, parse, or domain failure raises ``CatalogUnavailableError``; partial
    rows are never exposed (PRD risk: damaged catalog).
    """
    package = resources.files("lifescape").joinpath("data")
    try:
        manifest = json.loads(package.joinpath("place-catalog.manifest.json").read_text("utf-8"))
        compressed = package.joinpath(manifest["output_file"]).read_bytes()
    except (OSError, ValueError, KeyError) as exc:
        raise CatalogUnavailableError(f"catalog files are unreadable: {exc}") from exc
    return parse_catalog(compressed, manifest)


def parse_catalog(compressed: bytes, manifest: dict[str, Any]) -> PlaceCatalog:
    if hashlib.sha256(compressed).hexdigest() != manifest.get("output_sha256"):
        raise CatalogUnavailableError("catalog hash does not match its manifest")
    try:
        text = gzip.decompress(compressed).decode("utf-8")
        reader = csv.DictReader(io.StringIO(text))
        if tuple(reader.fieldnames or ()) != CATALOG_COLUMNS:
            raise CatalogUnavailableError("catalog columns do not match the contract")
        places = [_parse_place(row) for row in reader]
    except (OSError, EOFError, ValueError, UnicodeDecodeError, KeyError) as exc:
        raise CatalogUnavailableError(f"catalog could not be parsed: {exc}") from exc
    if len(places) != manifest.get("row_count"):
        raise CatalogUnavailableError("catalog row count does not match its manifest")
    if len({place.place_id for place in places}) != len(places):
        raise CatalogUnavailableError("catalog contains duplicate place identifiers")
    return PlaceCatalog(places, manifest)


def _parse_place(row: dict[str, str]) -> Place:
    values: dict[str, float | None] = {}
    for field in SCORED_FIELDS:
        raw = row[field]
        value = float(raw) if raw else None
        if value is not None and (not math.isfinite(value) or value < 0):
            raise ValueError(f"{field} must be a nonnegative number for {row['place_id']}")
        values[field] = value
    if row["state"] not in STATE_REGIONS or STATE_REGIONS[row["state"]] != row["region"]:
        raise ValueError(f"invalid state or region for {row['place_id']}")
    return Place(row["place_id"], row["name"], row["state"], row["region"], values)


def format_value(field_unit: str, value: float) -> str:
    if field_unit == "USD":
        return f"${value:,.0f}"
    if field_unit.startswith("percent"):
        return f"{value:.1f}%"
    if field_unit == "people per square mile":
        return f"{value:,.0f} people per sq mi"
    return f"{value:,.0f}"


class DiscoveryService:
    """``search(SearchProfile) -> result``: the single seam for all recommendation policy."""

    def __init__(self, catalog: PlaceCatalog) -> None:
        self.catalog = catalog

    # -- normalization -------------------------------------------------------------------

    def _normalize(self, field: str, raw: float) -> tuple[float, bool]:
        bounds = self.catalog.bounds[field]
        lower, upper = bounds["lower"], bounds["upper"]
        clipped = min(max(raw, lower), upper)
        span = upper - lower
        normalized = 0.0 if span <= 0 else (clipped - lower) / span
        return normalized, clipped != raw

    # -- profile resolution --------------------------------------------------------------

    def _resolve_exemplars(self, profile: SearchProfile) -> list[Place]:
        exemplars: list[Place] = []
        for place_id in profile.exemplars:
            place = self.catalog.places.get(place_id)
            if place is None:
                raise ProfileError(f"unknown exemplar town: {place_id}")
            if not place.serving_eligible:
                raise ProfileError(
                    f"{place.label} cannot be an example: Lifescape recommends from towns "
                    f"with a known population of {MINIMUM_SERVING_POPULATION:,} or more"
                )
            exemplars.append(place)
        return exemplars

    def resolve_targets(self, profile: SearchProfile, exemplars: list[Place]) -> dict[str, Any]:
        """Return ``{field: {"source", "explicit", "exemplar_values", "weight"}}``."""
        resolved: dict[str, Any] = {}
        for field in SCORED_FIELDS:
            exemplar_values = [
                (place, place.values[field])
                for place in exemplars
                if place.values[field] is not None
            ]
            if field in profile.targets:
                source = "user"
            elif exemplar_values:
                source = "exemplar"
            else:
                continue
            resolved[field] = {
                "source": source,
                "explicit": profile.targets.get(field),
                "exemplar_values": exemplar_values,
                "weight": profile.priorities.get(field, DEFAULT_WEIGHT),
            }
        unused = sorted(set(profile.priorities) - set(resolved))
        if unused:
            raise ProfileError(
                f"priority set for {', '.join(unused)} but nothing targets that quality; "
                "add a target or choose an example town that has it"
            )
        if len(resolved) < MINIMUM_MATCH_COMPONENTS:
            raise ProfileError(
                "this search needs at least two supported qualities to compare. Choose an "
                "example town with more data or add desired qualities"
            )
        return resolved

    # -- constraints ---------------------------------------------------------------------

    @staticmethod
    def _constraint_ids(profile: SearchProfile) -> list[str]:
        ids = [constraint.constraint_id for constraint in profile.hard_constraints]
        if profile.include_states:
            ids.append("state_include")
        if profile.exclude_states:
            ids.append("state_exclude")
        if profile.include_regions:
            ids.append("region_include")
        if profile.exclude_regions:
            ids.append("region_exclude")
        return sorted(ids)

    @staticmethod
    def _check_constraints(place: Place, profile: SearchProfile) -> tuple[list[str], list[str]]:
        """Return ``(failed ids, unknown ids)`` for a place."""
        failed: list[str] = []
        unknown: list[str] = []
        for constraint in profile.hard_constraints:
            value = place.values[constraint.field]
            if value is None:
                unknown.append(constraint.constraint_id)
            elif (constraint.operator == "min" and value < constraint.value) or (
                constraint.operator == "max" and value > constraint.value
            ):
                failed.append(constraint.constraint_id)
        if profile.include_states and place.state not in profile.include_states:
            failed.append("state_include")
        if profile.exclude_states and place.state in profile.exclude_states:
            failed.append("state_exclude")
        if profile.include_regions and place.region not in profile.include_regions:
            failed.append("region_include")
        if profile.exclude_regions and place.region in profile.exclude_regions:
            failed.append("region_exclude")
        return failed, unknown

    # -- scoring -------------------------------------------------------------------------

    def _component(
        self, place: Place, field: str, target: dict[str, Any], candidate_value: float
    ) -> dict[str, Any]:
        meta = self.catalog.fields[field]
        bounds = self.catalog.bounds[field]
        candidate_norm, candidate_clipped = self._normalize(field, candidate_value)
        if target["source"] == "user":
            options: list[tuple[Place | None, float]] = [(None, float(target["explicit"]))]
        else:
            options = [(p, float(v)) for p, v in target["exemplar_values"]]
        best: tuple[float, Place | None, float, float, bool] | None = None
        for exemplar, raw in options:
            target_norm, target_clipped = self._normalize(field, raw)
            similarity = 1 - abs(candidate_norm - target_norm)
            if best is None or similarity > best[0]:
                best = (similarity, exemplar, raw, target_norm, target_clipped)
        assert best is not None
        similarity, matched, target_raw, target_norm, target_clipped = best
        weight = target["weight"]
        return {
            "field": field,
            "label": meta["label"],
            "unit": meta["unit"],
            "definition": meta["definition"],
            "catalog_version": self.catalog.catalog_version,
            "target_source": target["source"],
            "available_targets": [
                {"place_id": p.place_id, "name": p.label, "value": v}
                for p, v in target["exemplar_values"]
            ]
            if target["source"] == "exemplar"
            else [],
            "matched_exemplar": (
                None if matched is None else {"place_id": matched.place_id, "name": matched.label}
            ),
            "target_value": target_raw,
            "candidate_value": candidate_value,
            "normalized_target": round(target_norm, 6),
            "normalized_candidate": round(candidate_norm, 6),
            "lower_bound": bounds["lower"],
            "upper_bound": bounds["upper"],
            "target_clipped": target_clipped,
            "candidate_clipped": candidate_clipped,
            "similarity": round(similarity, 6),
            "weight": weight,
            "weighted_similarity": round(weight * similarity, 6),
        }

    def _explain(
        self, components: list[dict[str, Any]]
    ) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
        """Build reasons and differences from structured components only (PRD FR9)."""
        by_strength = sorted(components, key=lambda c: (-c["similarity"], c["field"]))
        strong = [c for c in by_strength if c["similarity"] >= REASON_SIMILARITY_FLOOR]
        chosen = (strong if len(strong) >= MINIMUM_MATCH_COMPONENTS else by_strength)[:3]
        reasons = [
            self._reason_text(c, strong=c["similarity"] >= REASON_SIMILARITY_FLOOR) for c in chosen
        ]
        weakest = sorted(components, key=lambda c: (c["similarity"], c["field"]))
        gaps = [c for c in weakest if c["similarity"] < REASON_SIMILARITY_FLOOR][:3]
        differences = [self._difference_text(c) for c in (gaps or weakest[:1])]
        return reasons, differences

    @staticmethod
    def _describe_target(component: dict[str, Any]) -> str:
        unit = component["unit"]
        target = format_value(unit, component["target_value"])
        matched = component["matched_exemplar"]
        return f"{target} in {matched['name']}" if matched else f"your target of {target}"

    def _reason_text(self, component: dict[str, Any], *, strong: bool) -> dict[str, str]:
        candidate = format_value(component["unit"], component["candidate_value"])
        verb = "Close to" if strong else "Nearest available to"
        return {
            "field": component["field"],
            "text": (
                f"{component['label']}: {candidate}. {verb} {self._describe_target(component)}."
            ),
        }

    def _difference_text(self, component: dict[str, Any]) -> dict[str, str]:
        candidate = format_value(component["unit"], component["candidate_value"])
        text = f"{component['label']}: {candidate}, versus {self._describe_target(component)}."
        if component["candidate_clipped"] or component["target_clipped"]:
            text += " At least one value is beyond the catalog's typical range."
        return {"field": component["field"], "text": text}

    def _field_details(self, place: Place, resolved: dict[str, Any]) -> list[dict[str, Any]]:
        details = []
        for field in SCORED_FIELDS:
            meta = self.catalog.fields[field]
            value = place.values[field]
            target = resolved.get(field)
            details.append(
                {
                    "field": field,
                    "label": meta["label"],
                    "unit": meta["unit"],
                    "definition": meta["definition"],
                    "candidate_value": value,
                    "status": "missing" if value is None else "discovery data",
                    "evidence_status": "not verified evidence",
                    "targeted": target is not None,
                    "user_target": None if target is None else target["explicit"],
                    "exemplar_values": []
                    if target is None
                    else [
                        {"place_id": p.place_id, "name": p.label, "value": v}
                        for p, v in target["exemplar_values"]
                    ],
                }
            )
        return details

    # -- search --------------------------------------------------------------------------

    def search(
        self, profile: SearchProfile, limit: int = DEFAULT_RECOMMENDATION_LIMIT
    ) -> dict[str, Any]:
        exemplars = self._resolve_exemplars(profile)
        resolved = self.resolve_targets(profile, exemplars)
        total_weight = sum(target["weight"] for target in resolved.values())
        constraint_ids = self._constraint_ids(profile)
        exemplar_ids = {place.place_id for place in exemplars}
        unknown_excluded = sorted(set(profile.exclude_places) - set(self.catalog.places))
        if unknown_excluded:
            raise ProfileError(f"unknown excluded town: {', '.join(unknown_excluded)}")
        user_excluded_ids = set(profile.exclude_places)
        user_excluded = 0

        serving = self.catalog.serving_places
        missing_population = sum(1 for p in self.catalog.places.values() if p.population is None)
        below_minimum = sum(
            1
            for p in self.catalog.places.values()
            if p.population is not None and p.population < MINIMUM_SERVING_POPULATION
        )
        serving_exemplars = sum(1 for p in serving if p.place_id in exemplar_ids)
        excluded_counts = dict.fromkeys(constraint_ids, 0)
        unknown_counts = dict.fromkeys(constraint_ids, 0)
        excluded_any = 0
        insufficient = 0
        with_unknown = 0
        scored: list[tuple[float, int, str, dict[str, Any]]] = []

        for place in serving:
            if place.place_id in exemplar_ids:
                continue
            if place.place_id in user_excluded_ids:
                user_excluded += 1
                continue
            failed, unknown = self._check_constraints(place, profile)
            for constraint_id in failed:
                excluded_counts[constraint_id] += 1
            if failed:
                excluded_any += 1
                continue
            components = [
                self._component(place, field, target, value)
                for field, target in resolved.items()
                if (value := place.values[field]) is not None
            ]
            if len(components) < MINIMUM_MATCH_COMPONENTS:
                insufficient += 1
                continue
            for constraint_id in unknown:
                unknown_counts[constraint_id] += 1
            if unknown:
                with_unknown += 1
            numerator = sum(c["weight"] * c["similarity"] for c in components)
            total = round(numerator / total_weight, 6)
            for component in components:
                component["score_contribution"] = round(
                    component["weight"] * component["similarity"] / total_weight, 6
                )
            reasons, differences = self._explain(components)
            missing = [f for f in SCORED_FIELDS if place.values[f] is None]
            recommendation = {
                "place_id": place.place_id,
                "name": place.name,
                "state": place.state,
                "label": place.label,
                "population": place.population,
                "total_match": total,
                "match_percent": round(total * 100),
                "component_count": len(components),
                "profile_target_count": len(resolved),
                "components": components,
                "reasons": reasons,
                "differences": differences,
                "missing_fields": missing,
                "unknown_constraints": sorted(unknown),
                "fields": self._field_details(place, resolved),
                "catalog_version": self.catalog.catalog_version,
                "data_date": self.catalog.data_date,
                "evidence_status": "not verified evidence",
            }
            scored.append((-total, -len(components), place.place_id, recommendation))

        scored.sort(key=lambda item: item[:3])
        recommendable = len(scored)
        recommendations = [item[3] for item in scored[:limit]]
        for rank, recommendation in enumerate(recommendations, start=1):
            recommendation["rank"] = rank
        return {
            "profile": self._profile_echo(profile, exemplars, resolved),
            "catalog_version": self.catalog.catalog_version,
            "algorithm_version": ALGORITHM_VERSION,
            "normalization_version": NORMALIZATION_VERSION,
            "recommendations": recommendations,
            "diagnostics": {
                "catalog_places": len(self.catalog.places),
                "serving_places": len(serving),
                "serving_exemplar_count": serving_exemplars,
                "user_excluded_count": user_excluded,
                "missing_population_count": missing_population,
                "below_minimum_population_count": below_minimum,
                "known_constraint_exclusions": [
                    {
                        "constraint_id": constraint_id,
                        "excluded_count": excluded_counts[constraint_id],
                        "unknown_count": unknown_counts[constraint_id],
                    }
                    for constraint_id in constraint_ids
                ],
                "excluded_any_constraint_count": excluded_any,
                "insufficient_match_data_count": insufficient,
                "recommendable_with_unknown_constraints_count": with_unknown,
                "recommendable_count": recommendable,
                "returned_count": len(recommendations),
            },
        }

    def _profile_echo(
        self, profile: SearchProfile, exemplars: list[Place], resolved: dict[str, Any]
    ) -> dict[str, Any]:
        return {
            "exemplars": [
                {"place_id": p.place_id, "name": p.name, "state": p.state, "label": p.label}
                for p in exemplars
            ],
            "exclude_places": sorted(profile.exclude_places),
            "targets": [
                {
                    "field": field,
                    "label": self.catalog.fields[field]["label"],
                    "source": target["source"],
                    "value": target["explicit"],
                    "exemplar_values": [
                        {"place_id": p.place_id, "name": p.label, "value": v}
                        for p, v in target["exemplar_values"]
                    ],
                    "weight": target["weight"],
                }
                for field, target in resolved.items()
            ],
            "hard_constraints": [
                {
                    "constraint_id": c.constraint_id,
                    "field": c.field,
                    "operator": c.operator,
                    "value": c.value,
                }
                for c in sorted(profile.hard_constraints, key=lambda c: c.constraint_id)
            ],
            "include_states": list(profile.include_states),
            "exclude_states": list(profile.exclude_states),
            "include_regions": list(profile.include_regions),
            "exclude_regions": list(profile.exclude_regions),
        }
