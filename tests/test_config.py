from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from retirement_engine.config import ConfigurationError, load_configuration


def test_complete_configuration_is_loaded_and_profile_affects_hash(tmp_path: Path) -> None:
    baseline = load_configuration(Path("config"))
    profile = yaml.safe_load(Path("config/user_profile.example.yaml").read_text(encoding="utf-8"))
    profile["profile_version"] = "2.0"
    profile_path = tmp_path / "profile.yaml"
    profile_path.write_text(yaml.safe_dump(profile), encoding="utf-8")

    changed = load_configuration(Path("config"), profile_path)

    assert baseline.research_brief.regions == ("US",)
    assert baseline.user_profile.profile_version == "1.0"
    assert changed.user_profile.profile_version == "2.0"
    assert changed.config_hash != baseline.config_hash


def test_configuration_rejects_invalid_profile_budget(tmp_path: Path) -> None:
    profile = yaml.safe_load(Path("config/user_profile.example.yaml").read_text(encoding="utf-8"))
    profile["purchase_budget_min"] = profile["purchase_budget_max"] + 1
    profile_path = tmp_path / "profile.yaml"
    profile_path.write_text(yaml.safe_dump(profile), encoding="utf-8")

    with pytest.raises(ConfigurationError, match="purchase_budget_min"):
        load_configuration(Path("config"), profile_path)


def test_configuration_rejects_metric_geography_outside_research_scope(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    shutil.copytree("config", config_dir)
    brief_path = config_dir / "research_brief.yaml"
    brief = yaml.safe_load(brief_path.read_text(encoding="utf-8"))
    brief["scope"] = "county"
    brief_path.write_text(yaml.safe_dump(brief), encoding="utf-8")

    with pytest.raises(ConfigurationError, match="do not match research scope"):
        load_configuration(config_dir)


def test_configuration_rejects_gate_threshold_outside_metric_range(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    shutil.copytree("config", config_dir)
    gates_path = config_dir / "gates.default.yaml"
    gates = yaml.safe_load(gates_path.read_text(encoding="utf-8"))
    gates["gates"][1]["threshold"] = -1
    gates_path.write_text(yaml.safe_dump(gates), encoding="utf-8")

    with pytest.raises(ConfigurationError, match="gate thresholds fall outside"):
        load_configuration(config_dir)
