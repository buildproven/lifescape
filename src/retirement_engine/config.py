"""Strict YAML configuration loading and reproducibility hashing."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from retirement_engine.models import (
    GatesConfig,
    MetricDefinition,
    RegionsConfig,
    ResearchBrief,
    SourcesConfig,
    StrictModel,
    UserProfile,
    WeightsConfig,
)


class ConfigurationError(ValueError):
    """Raised when configuration is missing or invalid."""


def _read_yaml(path: Path) -> Any:
    try:
        with path.open(encoding="utf-8") as handle:
            return yaml.safe_load(handle)
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigurationError(f"cannot load {path}: {exc}") from exc


def _load[ModelT: BaseModel](path: Path, model: type[ModelT]) -> ModelT:
    try:
        return model.model_validate(_read_yaml(path))
    except ValueError as exc:
        raise ConfigurationError(f"invalid configuration {path}: {exc}") from exc


def load_weights(config_dir: Path) -> WeightsConfig:
    return _load(config_dir / "weights.default.yaml", WeightsConfig)


def load_gates(config_dir: Path) -> GatesConfig:
    return _load(config_dir / "gates.default.yaml", GatesConfig)


def load_sources(config_dir: Path) -> SourcesConfig:
    return _load(config_dir / "sources.yaml", SourcesConfig)


class MetricsFile(StrictModel):
    metrics: tuple[MetricDefinition, ...]


def load_metrics(config_dir: Path) -> tuple[MetricDefinition, ...]:
    return _load(config_dir / "metrics.yaml", MetricsFile).metrics


@dataclass(frozen=True)
class RuntimeConfig:
    research_brief: ResearchBrief
    user_profile: UserProfile
    regions: RegionsConfig
    weights: WeightsConfig
    gates: GatesConfig
    sources: SourcesConfig
    metrics: tuple[MetricDefinition, ...]
    config_hash: str


def _unique(values: list[str], label: str) -> None:
    duplicates = sorted({value for value in values if values.count(value) > 1})
    if duplicates:
        raise ConfigurationError(f"duplicate {label}: {duplicates}")


def load_configuration(config_dir: Path, profile_path: Path | None = None) -> RuntimeConfig:
    """Load and cross-validate the complete versioned decision configuration."""
    resolved_profile = profile_path or config_dir / "user_profile.example.yaml"
    research_brief = _load(config_dir / "research_brief.yaml", ResearchBrief)
    user_profile = _load(resolved_profile, UserProfile)
    regions = _load(config_dir / "regions.yaml", RegionsConfig)
    weights = load_weights(config_dir)
    gates = load_gates(config_dir)
    sources = load_sources(config_dir)
    metrics = load_metrics(config_dir)

    metric_ids = [metric.id for metric in metrics]
    gate_ids = [gate.id for gate in gates.gates]
    region_ids = [region.id for region in regions.regions]
    _unique(metric_ids, "metric ids")
    _unique(gate_ids, "gate ids")
    _unique(region_ids, "region ids")

    metric_id_set = set(metric_ids)
    unknown_gate_metrics = sorted(
        gate.metric_id for gate in gates.gates if gate.metric_id not in metric_id_set
    )
    if unknown_gate_metrics:
        raise ConfigurationError(f"gates reference unknown metrics: {unknown_gate_metrics}")
    metrics_by_id = {metric.id: metric for metric in metrics}
    invalid_gate_thresholds = sorted(
        gate.id
        for gate in gates.gates
        if gate.metric_id in metrics_by_id
        and not (
            metrics_by_id[gate.metric_id].valid_min
            <= gate.threshold
            <= metrics_by_id[gate.metric_id].valid_max
        )
    )
    if invalid_gate_thresholds:
        raise ConfigurationError(
            f"gate thresholds fall outside metric valid ranges: {invalid_gate_thresholds}"
        )
    noncritical_gates = sorted(gate.id for gate in gates.gates if not gate.critical)
    if noncritical_gates:
        raise ConfigurationError(f"hard gates must be critical: {noncritical_gates}")
    gated_metrics = {gate.metric_id for gate in gates.gates}
    ungated_critical_metrics = sorted(
        metric.id for metric in metrics if metric.critical and metric.id not in gated_metrics
    )
    if ungated_critical_metrics:
        raise ConfigurationError(f"critical metrics require hard gates: {ungated_critical_metrics}")
    metric_criteria = {metric.criterion for metric in metrics}
    weight_criteria = set(weights.weights)
    if metric_criteria != weight_criteria:
        raise ConfigurationError(
            "metric criteria and weight criteria differ: "
            f"missing weights={sorted(metric_criteria - weight_criteria)}, "
            f"unused weights={sorted(weight_criteria - metric_criteria)}"
        )
    unknown_regions = sorted(set(research_brief.regions) - set(region_ids))
    if unknown_regions:
        raise ConfigurationError(f"research brief references unknown regions: {unknown_regions}")
    scope_mismatches = sorted(
        metric.id for metric in metrics if metric.geography_level != research_brief.scope
    )
    if scope_mismatches:
        raise ConfigurationError(
            f"metrics do not match research scope {research_brief.scope!r}: {scope_mismatches}"
        )
    canonical = {
        "research_brief.yaml": _read_yaml(config_dir / "research_brief.yaml"),
        "user_profile.yaml": _read_yaml(resolved_profile),
        "regions.yaml": _read_yaml(config_dir / "regions.yaml"),
        "weights.default.yaml": _read_yaml(config_dir / "weights.default.yaml"),
        "gates.default.yaml": _read_yaml(config_dir / "gates.default.yaml"),
        "sources.yaml": _read_yaml(config_dir / "sources.yaml"),
        "metrics.yaml": _read_yaml(config_dir / "metrics.yaml"),
    }
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str)
    return RuntimeConfig(
        research_brief=research_brief,
        user_profile=user_profile,
        regions=regions,
        weights=weights,
        gates=gates,
        sources=sources,
        metrics=metrics,
        config_hash=hashlib.sha256(payload.encode()).hexdigest(),
    )


def configuration_hash(config_dir: Path, profile_path: Path | None = None) -> str:
    return load_configuration(config_dir, profile_path).config_hash
