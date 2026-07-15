"""Strict YAML configuration loading and reproducibility hashing."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from retirement_engine.models import (
    GatesConfig,
    MetricDefinition,
    SourcesConfig,
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


class MetricsFile(BaseModel):
    metrics: tuple[MetricDefinition, ...]


def load_metrics(config_dir: Path) -> tuple[MetricDefinition, ...]:
    return _load(config_dir / "metrics.yaml", MetricsFile).metrics


def configuration_hash(config_dir: Path) -> str:
    names = (
        "research_brief.yaml",
        "user_profile.example.yaml",
        "weights.default.yaml",
        "gates.default.yaml",
        "sources.yaml",
        "metrics.yaml",
    )
    canonical = {name: _read_yaml(config_dir / name) for name in names}
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode()).hexdigest()
