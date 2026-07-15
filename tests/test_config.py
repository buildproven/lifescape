from __future__ import annotations

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
