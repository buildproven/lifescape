"""Deterministic, advisory place discovery over a verified local catalog."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from importlib import resources
from math import ceil, isfinite
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Dimension = Literal[
    "population",
    "housing_cost",
    "population_density",
    "car_light_commute_share",
    "college_educated_share",
    "older_adult_share",
]
ConstraintOperator = Literal["min", "max", "eq"]

SUPPORTED_DIMENSIONS: tuple[Dimension, ...] = (
    "population",
    "housing_cost",
    "population_density",
    "car_light_commute_share",
    "college_educated_share",
    "older_adult_share",
)
DIMENSION_LABELS: dict[Dimension, str] = {
    "population": "Population",
    "housing_cost": "Housing cost",
    "population_density": "Population density",
    "car_light_commute_share": "Car-light commute share",
    "college_educated_share": "College-educated share",
    "older_adult_share": "Older-adult share",
}
DEFAULT_RECOMMENDATION_LIMIT = 10
MINIMUM_POPULATION = 2_500
ALGORITHM_VERSION = "place-discovery-v1"
NORMALIZATION_VERSION = "discovery-winsorized-minmax-v1"


class DiscoveryError(ValueError):
    """A user or catalog error that the HTTP adapter can expose safely."""


class CatalogUnavailableError(RuntimeError):
    """The packaged catalog cannot be trusted or loaded."""


class DiscoveryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DiscoveryTarget(DiscoveryModel):
    dimension: Dimension
    value: float = Field(ge=0, allow_inf_nan=False)
    weight: int = Field(default=3, ge=1, le=5)


class HardConstraint(DiscoveryModel):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")
    dimension: Dimension
    operator: ConstraintOperator
    value: float = Field(ge=0, allow_inf_nan=False)


class SearchProfile(DiscoveryModel):
    exemplar_place_ids: tuple[str, ...] = Field(default=(), max_length=2)
    targets: tuple[DiscoveryTarget, ...] = ()
    constraints: tuple[HardConstraint, ...] = ()
    include_states: tuple[str, ...] = ()
    exclude_states: tuple[str, ...] = ()

    @field_validator("exemplar_place_ids")
    @classmethod
    def exemplar_ids_are_unique(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("exemplar_place_ids must be unique")
        return values

    @field_validator("include_states", "exclude_states")
    @classmethod
    def states_are_normalized(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(value.strip().upper() for value in values)
        if any(len(value) != 2 or not value.isalpha() for value in normalized):
            raise ValueError("state filters must use two-letter state codes")
        if len(normalized) != len(set(normalized)):
            raise ValueError("state filters must be unique")
        return normalized

    @model_validator(mode="after")
    def targets_are_unique_and_filters_do_not_overlap(self) -> SearchProfile:
        dimensions = tuple(target.dimension for target in self.targets)
        if len(dimensions) != len(set(dimensions)):
            raise ValueError("targets must contain one entry per dimension")
        if set(self.include_states) & set(self.exclude_states):
            raise ValueError("a state cannot be both included and excluded")
        constraint_ids = tuple(constraint.id for constraint in self.constraints)
        if len(constraint_ids) != len(set(constraint_ids)):
            raise ValueError("constraints must have unique ids")
        return self


class CatalogPlace(DiscoveryModel):
    place_id: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=160)
    state: str = Field(min_length=2, max_length=2)
    geography_type: Literal["place"] = "place"
    values: dict[Dimension, float | None]

    @field_validator("state")
    @classmethod
    def state_is_uppercase(cls, value: str) -> str:
        normalized = value.upper()
        if not normalized.isalpha():
            raise ValueError("state must contain letters only")
        return normalized

    @field_validator("values")
    @classmethod
    def values_are_nonnegative(
        cls, values: dict[Dimension, float | None]
    ) -> dict[Dimension, float | None]:
        unknown = set(values) - set(SUPPORTED_DIMENSIONS)
        if unknown:
            raise ValueError(f"unsupported discovery dimensions: {sorted(unknown)}")
        if any(
            value is not None and (value < 0 or not isfinite(value)) for value in values.values()
        ):
            raise ValueError("catalog values must be finite and nonnegative")
        return values

    @property
    def population(self) -> float | None:
        return self.values.get("population")

    @property
    def serving_eligible(self) -> bool:
        return self.population is not None and self.population >= MINIMUM_POPULATION


class NormalizationBound(DiscoveryModel):
    lower: float = Field(ge=0, allow_inf_nan=False)
    upper: float = Field(ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def bounds_are_ordered(self) -> NormalizationBound:
        if self.lower > self.upper:
            raise ValueError("normalization lower bound cannot exceed upper bound")
        return self


class CatalogManifest(DiscoveryModel):
    catalog_version: str = Field(min_length=1)
    algorithm_version: str = ALGORITHM_VERSION
    normalization_version: str = NORMALIZATION_VERSION
    data_date: date
    row_count: int = Field(ge=0)
    output_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_urls: tuple[str, ...] = ()
    source_vintage: str = "unknown"
    normalization_bounds: dict[Dimension, NormalizationBound]
    coverage: dict[Dimension, float]
    source_hashes: dict[str, dict[str, str]] = Field(default_factory=dict)
    selected_fields: dict[str, str] = Field(default_factory=dict)
    missing_value_policy: str = ""

    @field_validator("coverage")
    @classmethod
    def coverage_is_a_fraction(cls, values: dict[Dimension, float]) -> dict[Dimension, float]:
        if any(value < 0 or value > 1 or not isfinite(value) for value in values.values()):
            raise ValueError("catalog coverage must be between zero and one")
        return values


class PlaceLookup(DiscoveryModel):
    place_id: str
    name: str
    state: str
    population: float | None
    serving_eligible: bool


class PlaceLookupResult(DiscoveryModel):
    query: str
    catalog_version: str
    places: tuple[PlaceLookup, ...]


class ResolvedTarget(DiscoveryModel):
    dimension: Dimension
    weight: int
    target_values: tuple[float, ...]
    source: Literal["explicit", "exemplar"]


class DiscoveryComponent(DiscoveryModel):
    dimension: Dimension
    label: str
    candidate_value: float
    target_value: float
    target_values: tuple[float, ...]
    normalized_candidate: float
    normalized_target: float
    similarity: float
    weight: int
    weighted_contribution: float
    lower_bound: float
    upper_bound: float
    target_clipped: bool
    candidate_clipped: bool
    matched_exemplar_index: int | None = None


class DiscoveryRecommendation(DiscoveryModel):
    place_id: str
    name: str
    state: str
    population: float
    total_match: float
    component_count: int
    components: tuple[DiscoveryComponent, ...]
    reasons: tuple[str, ...]
    tradeoffs: tuple[str, ...]
    missing_fields: tuple[Dimension, ...]
    unknown_constraints: tuple[str, ...]
    catalog_version: str
    data_date: date


class ConstraintDiagnostic(DiscoveryModel):
    constraint_id: str
    excluded_count: int = Field(ge=0)
    unknown_count: int = Field(ge=0)


class DiscoveryDiagnostics(DiscoveryModel):
    catalog_places: int = Field(ge=0)
    serving_places: int = Field(ge=0)
    serving_exemplar_count: int = Field(ge=0)
    missing_population_count: int = Field(ge=0)
    below_minimum_population_count: int = Field(ge=0)
    known_constraint_exclusions: tuple[ConstraintDiagnostic, ...]
    excluded_any_constraint_count: int = Field(ge=0)
    insufficient_match_data_count: int = Field(ge=0)
    recommendable_with_unknown_constraints_count: int = Field(ge=0)
    recommendable_count: int = Field(ge=0)
    returned_count: int = Field(ge=0)


class DiscoveryResult(DiscoveryModel):
    profile: SearchProfile
    resolved_targets: tuple[ResolvedTarget, ...]
    catalog_version: str
    algorithm_version: str
    normalization_version: str
    recommendations: tuple[DiscoveryRecommendation, ...]
    diagnostics: DiscoveryDiagnostics


def serialize_catalog(places: tuple[CatalogPlace, ...]) -> bytes:
    """Return the canonical catalog bytes used for manifest hashing."""
    payload = [place.model_dump(mode="json") for place in places]
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _nearest_rank(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, ceil(percentile * len(ordered)) - 1))
    return ordered[index]


class DiscoveryCatalog:
    """An immutable catalog whose manifest and rows have already been verified."""

    def __init__(self, places: tuple[CatalogPlace, ...], manifest: CatalogManifest) -> None:
        if manifest.row_count != len(places):
            raise CatalogUnavailableError("CATALOG_UNAVAILABLE: row count does not match manifest")
        if len({place.place_id for place in places}) != len(places):
            raise CatalogUnavailableError("CATALOG_UNAVAILABLE: duplicate place_id")
        self.places = places
        self.manifest = manifest
        self.by_id = {place.place_id: place for place in places}

    @classmethod
    def from_places(
        cls,
        places: tuple[CatalogPlace, ...],
        *,
        catalog_version: str = "fixture",
        data_date: date = date(2023, 12, 31),
        source_urls: tuple[str, ...] = (),
        source_vintage: str = "fixture",
    ) -> DiscoveryCatalog:
        serving = [place for place in places if place.serving_eligible]
        bounds: dict[Dimension, NormalizationBound] = {}
        coverage: dict[Dimension, float] = {}
        for dimension in SUPPORTED_DIMENSIONS:
            present = [place.values.get(dimension) for place in serving]
            values = [value for value in present if value is not None]
            coverage[dimension] = len(values) / len(serving) if serving else 0.0
            if not values:
                bounds[dimension] = NormalizationBound(lower=0, upper=0)
            else:
                bounds[dimension] = NormalizationBound(
                    lower=_nearest_rank(values, 0.05),
                    upper=_nearest_rank(values, 0.95),
                )
        catalog_bytes = serialize_catalog(places)
        manifest = CatalogManifest(
            catalog_version=catalog_version,
            data_date=data_date,
            row_count=len(places),
            output_sha256=hashlib.sha256(catalog_bytes).hexdigest(),
            source_urls=source_urls,
            source_vintage=source_vintage,
            normalization_bounds=bounds,
            coverage=coverage,
        )
        return cls(places, manifest)

    @classmethod
    def from_paths(cls, catalog_path: Path, manifest_path: Path) -> DiscoveryCatalog:
        try:
            catalog_bytes = catalog_path.read_bytes()
            raw_places = json.loads(catalog_bytes)
            raw_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            places = tuple(CatalogPlace.model_validate(item) for item in raw_places)
            manifest = CatalogManifest.model_validate(raw_manifest)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise CatalogUnavailableError(
                f"CATALOG_UNAVAILABLE: cannot load catalog: {exc}"
            ) from exc
        actual_hash = hashlib.sha256(catalog_bytes).hexdigest()
        if actual_hash != manifest.output_sha256:
            raise CatalogUnavailableError("CATALOG_UNAVAILABLE: catalog hash mismatch")
        if len(places) != manifest.row_count:
            raise CatalogUnavailableError("CATALOG_UNAVAILABLE: catalog row count mismatch")
        return cls(places, manifest)


class PlaceDiscoveryService:
    """Resolve household intent into deterministic, advisory place recommendations."""

    def __init__(self, catalog: DiscoveryCatalog) -> None:
        self.catalog = catalog

    def validate(self, profile: SearchProfile | dict[str, object]) -> SearchProfile:
        parsed = (
            profile if isinstance(profile, SearchProfile) else SearchProfile.model_validate(profile)
        )
        self._resolve_targets(parsed)
        return parsed

    def lookup(self, query: str, limit: int = DEFAULT_RECOMMENDATION_LIMIT) -> PlaceLookupResult:
        normalized = query.strip()
        if not 2 <= len(normalized) <= 120:
            raise DiscoveryError("query must contain between 2 and 120 characters")
        if not 1 <= limit <= 20:
            raise DiscoveryError("limit must be between 1 and 20")
        folded = normalized.casefold()
        matches = [
            PlaceLookup(
                place_id=place.place_id,
                name=place.name,
                state=place.state,
                population=place.population,
                serving_eligible=place.serving_eligible,
            )
            for place in sorted(
                self.catalog.places,
                key=lambda item: (item.name.casefold(), item.state, item.place_id),
            )
            if folded in f"{place.name}, {place.state}".casefold()
        ][:limit]
        return PlaceLookupResult(
            query=normalized,
            catalog_version=self.catalog.manifest.catalog_version,
            places=tuple(matches),
        )

    def search(self, profile: SearchProfile | dict[str, object]) -> DiscoveryResult:
        parsed = self.validate(profile)
        resolved_targets = self._resolve_targets(parsed)
        exemplars = tuple(self.catalog.by_id[place_id] for place_id in parsed.exemplar_place_ids)
        serving_places = tuple(place for place in self.catalog.places if place.serving_eligible)
        exemplar_ids = {place.place_id for place in exemplars}
        constraint_diagnostics: list[ConstraintDiagnostic] = []
        excluded_by_constraint: dict[str, set[str]] = {}
        unknown_by_constraint: dict[str, set[str]] = {}
        for constraint in parsed.constraints:
            excluded: set[str] = set()
            unknown: set[str] = set()
            for place in serving_places:
                if place.place_id in exemplar_ids:
                    continue
                value = place.values.get(constraint.dimension)
                if value is None:
                    unknown.add(place.place_id)
                elif not self._constraint_passes(value, constraint):
                    excluded.add(place.place_id)
            excluded_by_constraint[constraint.id] = excluded
            unknown_by_constraint[constraint.id] = unknown
            constraint_diagnostics.append(
                ConstraintDiagnostic(
                    constraint_id=constraint.id,
                    excluded_count=len(excluded),
                    unknown_count=len(unknown),
                )
            )

        excluded_any = (
            set().union(*excluded_by_constraint.values()) if excluded_by_constraint else set()
        )
        candidates = []
        insufficient = 0
        recommendable_unknown: set[str] = set()
        for place in serving_places:
            if place.place_id in exemplar_ids or place.place_id in excluded_any:
                continue
            if parsed.include_states and place.state not in parsed.include_states:
                continue
            if place.state in parsed.exclude_states:
                continue
            recommendation = self._recommendation(
                place, resolved_targets, parsed, unknown_by_constraint
            )
            if recommendation is None:
                insufficient += 1
                continue
            if recommendation.unknown_constraints:
                recommendable_unknown.add(place.place_id)
            candidates.append(recommendation)
        candidates.sort(key=lambda item: (-item.total_match, -item.component_count, item.place_id))
        returned = tuple(candidates[:DEFAULT_RECOMMENDATION_LIMIT])
        diagnostics = DiscoveryDiagnostics(
            catalog_places=len(self.catalog.places),
            serving_places=len(serving_places),
            serving_exemplar_count=len(exemplar_ids),
            missing_population_count=sum(place.population is None for place in self.catalog.places),
            below_minimum_population_count=sum(
                place.population is not None and place.population < MINIMUM_POPULATION
                for place in self.catalog.places
            ),
            known_constraint_exclusions=tuple(
                sorted(constraint_diagnostics, key=lambda item: item.constraint_id)
            ),
            excluded_any_constraint_count=len(excluded_any),
            insufficient_match_data_count=insufficient,
            recommendable_with_unknown_constraints_count=len(recommendable_unknown),
            recommendable_count=len(candidates),
            returned_count=len(returned),
        )
        return DiscoveryResult(
            profile=parsed,
            resolved_targets=resolved_targets,
            catalog_version=self.catalog.manifest.catalog_version,
            algorithm_version=self.catalog.manifest.algorithm_version,
            normalization_version=self.catalog.manifest.normalization_version,
            recommendations=returned,
            diagnostics=diagnostics,
        )

    def _resolve_targets(self, profile: SearchProfile) -> tuple[ResolvedTarget, ...]:
        explicit = {target.dimension: target for target in profile.targets}
        exemplars: list[CatalogPlace] = []
        for place_id in profile.exemplar_place_ids:
            place = self.catalog.by_id.get(place_id)
            if place is None:
                raise DiscoveryError(f"unknown exemplar place_id: {place_id}")
            if not place.serving_eligible:
                raise DiscoveryError(
                    f"exemplar is below the serving population threshold: {place_id}"
                )
            exemplars.append(place)
        resolved: list[ResolvedTarget] = []
        for dimension in SUPPORTED_DIMENSIONS:
            if dimension in explicit:
                target = explicit[dimension]
                resolved.append(
                    ResolvedTarget(
                        dimension=dimension,
                        weight=target.weight,
                        target_values=(target.value,),
                        source="explicit",
                    )
                )
                continue
            values = tuple(
                value for place in exemplars if (value := place.values.get(dimension)) is not None
            )
            if values:
                resolved.append(
                    ResolvedTarget(
                        dimension=dimension,
                        weight=3,
                        target_values=values,
                        source="exemplar",
                    )
                )
        if len(resolved) < 2:
            raise DiscoveryError(
                "profile must resolve at least two supported qualities; add targets or choose "
                "another exemplar"
            )
        return tuple(resolved)

    def _recommendation(
        self,
        place: CatalogPlace,
        targets: tuple[ResolvedTarget, ...],
        profile: SearchProfile,
        unknown_by_constraint: dict[str, set[str]],
    ) -> DiscoveryRecommendation | None:
        components: list[DiscoveryComponent] = []
        missing_fields: list[Dimension] = []
        total_weight = sum(target.weight for target in targets)
        weighted_total = 0.0
        for target in targets:
            candidate_value = place.values.get(target.dimension)
            if candidate_value is None:
                missing_fields.append(target.dimension)
                continue
            bound = self.catalog.manifest.normalization_bounds[target.dimension]
            normalized_candidate, candidate_clipped = self._normalize(candidate_value, bound)
            matches = [self._normalize(value, bound) for value in target.target_values]
            similarities = [1 - abs(normalized_candidate - normalized) for normalized, _ in matches]
            best_index = max(range(len(similarities)), key=lambda index: similarities[index])
            normalized_target, target_clipped = matches[best_index]
            similarity = similarities[best_index]
            contribution = target.weight * similarity
            weighted_total += contribution
            components.append(
                DiscoveryComponent(
                    dimension=target.dimension,
                    label=DIMENSION_LABELS[target.dimension],
                    candidate_value=candidate_value,
                    target_value=target.target_values[best_index],
                    target_values=target.target_values,
                    normalized_candidate=normalized_candidate,
                    normalized_target=normalized_target,
                    similarity=similarity,
                    weight=target.weight,
                    weighted_contribution=contribution,
                    lower_bound=bound.lower,
                    upper_bound=bound.upper,
                    target_clipped=target_clipped,
                    candidate_clipped=candidate_clipped,
                    matched_exemplar_index=(best_index if target.source == "exemplar" else None),
                )
            )
        if len(components) < 2:
            return None
        unknown_constraints = tuple(
            constraint.id
            for constraint in profile.constraints
            if place.place_id in unknown_by_constraint[constraint.id]
        )
        ordered_components = tuple(sorted(components, key=lambda item: item.dimension))
        reasons = tuple(
            f"{component.label} match: {component.similarity:.2f} similarity "
            f"(candidate {component.candidate_value:g}; target {component.target_value:g})"
            for component in sorted(components, key=lambda item: (-item.similarity, item.dimension))
        )
        tradeoffs = [
            f"Weakest measured match: {component.label} at {component.similarity:.2f} similarity."
            for component in sorted(components, key=lambda item: (item.similarity, item.dimension))[
                :1
            ]
        ]
        tradeoffs.extend(
            f"Needs verification: {DIMENSION_LABELS[dimension]} is missing from discovery data."
            for dimension in missing_fields
        )
        return DiscoveryRecommendation(
            place_id=place.place_id,
            name=place.name,
            state=place.state,
            population=cast(float, place.population),
            total_match=weighted_total / total_weight,
            component_count=len(ordered_components),
            components=ordered_components,
            reasons=reasons,
            tradeoffs=tuple(tradeoffs),
            missing_fields=tuple(missing_fields),
            unknown_constraints=unknown_constraints,
            catalog_version=self.catalog.manifest.catalog_version,
            data_date=self.catalog.manifest.data_date,
        )

    @staticmethod
    def _constraint_passes(value: float, constraint: HardConstraint) -> bool:
        if constraint.operator == "min":
            return value >= constraint.value
        if constraint.operator == "max":
            return value <= constraint.value
        return value == constraint.value

    @staticmethod
    def _normalize(value: float, bound: NormalizationBound) -> tuple[float, bool]:
        if bound.lower == bound.upper:
            return 0.5, value != bound.lower
        clipped = min(bound.upper, max(bound.lower, value))
        return (clipped - bound.lower) / (bound.upper - bound.lower), clipped != value


def load_default_discovery_catalog(repository_root: Path | None = None) -> DiscoveryCatalog:
    """Load the checked-in catalog without allowing a partial fallback."""
    if repository_root is not None:
        return DiscoveryCatalog.from_paths(
            repository_root / "data/discovery/catalog.json",
            repository_root / "data/discovery/catalog-manifest.json",
        )
    repository = Path(__file__).resolve().parents[2]
    repository_catalog = repository / "data/discovery/catalog.json"
    repository_manifest = repository / "data/discovery/catalog-manifest.json"
    if repository_catalog.is_file() and repository_manifest.is_file():
        return DiscoveryCatalog.from_paths(repository_catalog, repository_manifest)
    package_root = resources.files("lifescape").joinpath("resources", "discovery")
    catalog_resource = package_root.joinpath("catalog.json")
    manifest_resource = package_root.joinpath("catalog-manifest.json")
    try:
        with (
            resources.as_file(catalog_resource) as catalog_path,
            resources.as_file(manifest_resource) as manifest_path,
        ):
            return DiscoveryCatalog.from_paths(catalog_path, manifest_path)
    except (FileNotFoundError, OSError) as exc:
        raise CatalogUnavailableError(
            f"CATALOG_UNAVAILABLE: packaged discovery catalog is unavailable: {exc}"
        ) from exc
