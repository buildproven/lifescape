"""Typed domain contracts shared across the engine."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from math import isfinite

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SourceTier(StrEnum):
    A = "A"
    B = "B"
    C = "C"


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class GateState(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"
    WAIVED = "WAIVED"


class Direction(StrEnum):
    HIGHER = "higher"
    LOWER = "lower"


class GateOperator(StrEnum):
    MIN = "min"
    MAX = "max"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MetricDefinition(StrictModel):
    id: str
    name: str
    unit: str
    direction: Direction
    geography_level: str = "town"
    criterion: str
    critical: bool = False
    freshness_days: int = Field(default=730, ge=0)


class GateDefinition(StrictModel):
    id: str
    metric_id: str
    operator: GateOperator
    threshold: float = Field(allow_inf_nan=False)
    critical: bool = True


class SourceRecord(StrictModel):
    url: str
    title: str
    publisher: str
    tier: SourceTier
    retrieved_at: date
    geography: str
    confidence: Confidence
    synthetic: bool = False


class PlaceRecord(StrictModel):
    place_id: str
    name: str
    state: str = Field(min_length=2, max_length=2)
    geography_type: str = "town"


class ObservationRecord(StrictModel):
    place: PlaceRecord
    metric_id: str
    raw_value: float = Field(allow_inf_nan=False)
    observed_period: str
    observed_at: date
    source: SourceRecord


class GateResult(StrictModel):
    place_id: str
    gate_id: str
    result: GateState
    raw_value: float | None
    threshold: float
    source_url: str | None
    notes: str


class CriterionScore(StrictModel):
    place_id: str
    criterion: str
    normalized_score: float
    weight: float
    weighted_score: float
    missing_penalty: float = 0.0
    missing_critical: bool = False
    source_urls: tuple[str, ...] = ()


class PlaceScore(StrictModel):
    place_id: str
    total_score: float
    rank: int
    criteria: tuple[CriterionScore, ...]


class SensitivityResult(StrictModel):
    place_id: str
    top_three_frequency: float
    mean_rank: float
    rank_variance: float
    fragile: bool


class RunResult(StrictModel):
    run_id: str
    profile_version: str
    config_hash: str
    engine_version: str
    evaluated_as_of: date
    evidence_through: datetime
    simulations: int
    sensitivity_seed: int
    persisted: bool = False
    places: tuple[PlaceRecord, ...]
    observations: tuple[ObservationRecord, ...]
    gate_results: tuple[GateResult, ...]
    scores: tuple[PlaceScore, ...]
    sensitivity: tuple[SensitivityResult, ...]


class WeightsConfig(StrictModel):
    weights: dict[str, float]
    missing_noncritical_penalty: float = Field(default=2.0, ge=0, le=10)

    @model_validator(mode="after")
    def total_is_one_hundred(self) -> WeightsConfig:
        if any(not isfinite(value) for value in self.weights.values()):
            raise ValueError("weights must be finite")
        if abs(sum(self.weights.values()) - 100.0) > 1e-8:
            raise ValueError("weights must total 100")
        if any(value < 0 for value in self.weights.values()):
            raise ValueError("weights cannot be negative")
        return self


class GatesConfig(StrictModel):
    gates: tuple[GateDefinition, ...]


class SourcesConfig(StrictModel):
    allowed_scoring_tiers: frozenset[SourceTier]
    discovery_only_tiers: frozenset[SourceTier]
    minimum_gate_confidence: Confidence
    max_age_days: int = Field(ge=0)

    @model_validator(mode="after")
    def scoring_and_discovery_tiers_are_disjoint(self) -> SourcesConfig:
        overlap = self.allowed_scoring_tiers & self.discovery_only_tiers
        if overlap:
            raise ValueError(
                f"source tiers cannot be both scoring and discovery-only: {sorted(overlap)}"
            )
        return self


class ResearchBrief(StrictModel):
    name: str
    scope: str
    regions: tuple[str, ...]
    benchmark_only: bool
    frozen: bool


class UserProfile(StrictModel):
    profile_version: str
    purchase_budget_min: float = Field(ge=0)
    purchase_budget_max: float = Field(ge=0)
    max_annual_carrying_cost: float = Field(ge=0)
    household: str
    future_self_ages: tuple[int, ...]
    priorities: tuple[str, ...]

    @model_validator(mode="after")
    def budget_range_is_ordered(self) -> UserProfile:
        if self.purchase_budget_min > self.purchase_budget_max:
            raise ValueError("purchase_budget_min cannot exceed purchase_budget_max")
        return self


class RegionDefinition(StrictModel):
    id: str
    states: str | tuple[str, ...]

    @model_validator(mode="after")
    def string_scope_is_wildcard(self) -> RegionDefinition:
        if isinstance(self.states, str) and self.states != "*":
            raise ValueError("region states must be '*' or a list of state codes")
        return self


class RegionsConfig(StrictModel):
    regions: tuple[RegionDefinition, ...]
