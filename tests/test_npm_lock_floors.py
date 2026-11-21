"""BUI-2061: locked npm dev packages stay at or above their first patched release.

Dependabot flagged `tmp` (high, fixed 0.2.6; low, fixed 0.2.4), pulled in by
`external-editor` through the commitlint prompt tooling. A `package.json`
`overrides` entry raises it; this test fails if the lockfile drops below the floor.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LOCKFILE = ROOT / "package-lock.json"

# Requirement BUI-2061: every locked `tmp` version shall be >= 0.2.6.
FLOORS = {"tmp": (0, 2, 6)}


def parse(text: str) -> tuple[int, ...]:
    core = text.split("+")[0].split("-")[0]
    return tuple(int(part) for part in core.split(".")[:3])


def locked_versions(lock: dict[str, object], name: str) -> list[tuple[str, str]]:
    packages = lock.get("packages")
    assert isinstance(packages, dict), "package-lock.json has no 'packages' map"
    found: list[tuple[str, str]] = []
    for path, entry in packages.items():
        if path == f"node_modules/{name}" or path.endswith(f"/node_modules/{name}"):
            assert isinstance(entry, dict) and isinstance(entry.get("version"), str), path
            found.append((path, entry["version"]))
    return found


@pytest.mark.parametrize("name", sorted(FLOORS))
def test_bui_2061_every_locked_copy_meets_floor(name: str) -> None:
    lock = json.loads(LOCKFILE.read_text(encoding="utf-8"))
    found = locked_versions(lock, name)
    assert found, f"{name} not found in package-lock.json"
    low = [(path, ver) for path, ver in found if parse(ver) < FLOORS[name]]
    assert not low, f"{name} below {'.'.join(map(str, FLOORS[name]))}: {low}"


def test_bui_2061_nested_copies_are_checked() -> None:
    lock = {
        "packages": {
            "node_modules/tmp": {"version": "0.2.6"},
            "node_modules/a/node_modules/tmp": {"version": "0.0.33"},
            "node_modules/tmpdir": {"version": "0.0.1"},
        }
    }
    found = locked_versions(lock, "tmp")
    assert sorted(found) == [
        ("node_modules/a/node_modules/tmp", "0.0.33"),
        ("node_modules/tmp", "0.2.6"),
    ]
    assert parse("0.0.33") < FLOORS["tmp"]
    assert parse("0.2.5") < FLOORS["tmp"]
    assert parse("0.2.6") >= FLOORS["tmp"]
    assert parse("1.0.0-rc.1") >= FLOORS["tmp"]
