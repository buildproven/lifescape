"""The runtime imports jwt and httpx2 directly, so the project declares them directly.

Without this, the packages arrive only through mcp's own requirements, and a
future mcp release that drops either one would break the hosted OAuth server.
"""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).resolve().parents[1]
SOURCE = REPOSITORY / "src" / "lifescape"

# Import name -> distribution name for the packages this card declares.
DIRECT = {"jwt": "pyjwt", "httpx2": "httpx2"}


def _requirement_name(requirement: str) -> str:
    match = re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", requirement)
    assert match, requirement
    return re.sub(r"[-_.]+", "-", match.group(0)).lower()


def _pyproject() -> dict[str, object]:
    return tomllib.loads((REPOSITORY / "pyproject.toml").read_text(encoding="utf-8"))


def _runtime_requirements() -> dict[str, str]:
    project = _pyproject()["project"]
    assert isinstance(project, dict)
    return {_requirement_name(item): item for item in project["dependencies"]}


def _dev_requirements() -> set[str]:
    project = _pyproject()["project"]
    assert isinstance(project, dict)
    return {_requirement_name(item) for item in project["optional-dependencies"]["dev"]}


def _lifescape_lock_entry() -> dict[str, object]:
    lock = tomllib.loads((REPOSITORY / "uv.lock").read_text(encoding="utf-8"))
    for package in lock["package"]:
        if package["name"] == "lifescape":
            assert isinstance(package, dict)
            return package
    raise AssertionError("uv.lock has no lifescape package")


def _imported_top_level_modules() -> set[str]:
    modules: set[str] = set()
    for path in SOURCE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules.add(node.module.split(".")[0])
    return modules


@pytest.mark.parametrize(("module", "distribution"), sorted(DIRECT.items()))
def test_runtime_source_imports_the_package(module: str, distribution: str) -> None:
    assert module in _imported_top_level_modules(), distribution


@pytest.mark.parametrize("distribution", sorted(DIRECT.values()))
def test_pyproject_declares_package_as_runtime_dependency(distribution: str) -> None:
    runtime = _runtime_requirements()
    assert distribution in runtime
    assert ";" not in runtime[distribution], "a runtime dependency has no environment marker"
    assert distribution not in _dev_requirements(), "declare it once, as a runtime dependency"


@pytest.mark.parametrize(
    ("distribution", "floor"),
    [("pyjwt", ">=2.10.1"), ("httpx2", ">=2.10")],
)
def test_runtime_floor_is_not_below_what_mcp_requires(distribution: str, floor: str) -> None:
    assert floor in _runtime_requirements()[distribution]


@pytest.mark.parametrize("distribution", sorted(DIRECT.values()))
def test_lockfile_records_package_as_direct_runtime_dependency(distribution: str) -> None:
    entry = _lifescape_lock_entry()
    dependencies = entry["dependencies"]
    assert isinstance(dependencies, list)
    assert distribution in {item["name"] for item in dependencies}

    metadata = entry["metadata"]
    assert isinstance(metadata, dict)
    declared = [item for item in metadata["requires-dist"] if item["name"] == distribution]
    assert len(declared) == 1
    assert "marker" not in declared[0], "the lock records it without the dev extra"
