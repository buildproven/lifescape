"""``bundled_config`` yields the packaged configuration without the benchmark evidence."""

from __future__ import annotations

from pathlib import Path

import pytest

from lifescape import resources as resources_module
from lifescape.config import load_gates, load_metrics, load_sources
from lifescape.resources import bundled_benchmark, bundled_config


class _Missing:
    def joinpath(self, *_: str) -> _Missing:
        return self

    def is_dir(self) -> bool:
        return False

    def is_file(self) -> bool:
        return False


def test_bundled_config_matches_the_benchmark_configuration() -> None:
    with bundled_benchmark() as (_, benchmark_config), bundled_config() as config:
        assert config == benchmark_config
        assert config.is_dir()
        assert load_metrics(config)
        assert load_sources(config)
        assert load_gates(config).gates


def test_bundled_config_prefers_the_packaged_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    package = tmp_path / "lifescape"
    packaged_config = package / "resources" / "config"
    packaged_config.mkdir(parents=True)
    # No benchmark evidence is packaged: the config helper must not need it.
    monkeypatch.setattr(resources_module.resources, "files", lambda _: package)

    with bundled_config() as config:
        assert config == packaged_config


def test_bundled_config_falls_back_to_the_checkout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(resources_module.resources, "files", lambda _: _Missing())
    checkout_config = Path(resources_module.__file__).resolve().parents[2] / "config"

    with bundled_config() as config:
        assert config == checkout_config


def test_bundled_config_reports_missing_resources(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(resources_module.resources, "files", lambda _: _Missing())
    fake_module = tmp_path / "src" / "lifescape" / "resources.py"
    monkeypatch.setattr(resources_module, "__file__", str(fake_module))

    with (
        pytest.raises(FileNotFoundError, match="configuration resources are unavailable"),
        bundled_config(),
    ):
        pass
