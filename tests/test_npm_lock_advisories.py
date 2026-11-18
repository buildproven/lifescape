"""REQ-DEPS-1: the npm lockfile never resolves dev tooling below its advisory fix floors.

`npm run security:npm` audits production dependencies only, so a dev-only advisory (stylelint,
ajv, cosmiconfig chains) never fails a check. This floor test keeps the patched versions from
regressing in ``package-lock.json``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

LOCKFILE = Path(__file__).resolve().parents[1] / "package-lock.json"

# name -> (major line the floor applies to, or None for every line; first patched release).
FLOORS: dict[str, tuple[int | None, tuple[int, int, int]]] = {
    "fast-uri": (None, (3, 1, 9)),
    "js-yaml": (4, (4, 3, 2)),
    "source-map-js": (None, (1, 2, 2)),
    "brace-expansion": (5, (5, 0, 12)),
    "postcss": (None, (8, 5, 23)),
    "postcss-selector-parser": (7, (7, 1, 6)),
}


def parse(version: str) -> tuple[int, int, int, int]:
    """Return ``(major, minor, patch, release)``; a prerelease (``release`` 0) sorts below it."""
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?", version)
    if match is None:
        raise ValueError(f"unparseable version {version!r}")
    major, minor, patch = (int(part) for part in match.groups()[:3])
    return major, minor, patch, 0 if match.group(4) else 1


def resolved(lock: dict[str, Any], name: str) -> dict[str, str]:
    """Return ``{packages key: version}`` for every installed copy of ``name``."""
    suffix = f"node_modules/{name}"
    return {
        key: entry["version"]
        for key, entry in lock["packages"].items()
        if key == suffix or key.endswith(f"/{suffix}")
    }


def violations(lock: dict[str, Any]) -> list[str]:
    problems = []
    for name, (line, floor) in FLOORS.items():
        for key, version in resolved(lock, name).items():
            parsed = parse(version)
            if (line is None or parsed[0] == line) and parsed < (*floor, 1):
                wanted = ".".join(map(str, floor))
                problems.append(f"{key} {version} < {wanted}")
    return problems


def test_req_deps_1_lockfile_resolves_every_advisory_package_at_or_above_its_floor() -> None:
    lock = json.loads(LOCKFILE.read_text(encoding="utf-8"))

    for name in FLOORS:
        assert resolved(lock, name), f"{name} not found in package-lock.json; update FLOORS"
    assert not violations(lock), "\n".join(violations(lock))


def test_req_deps_1_floor_check_flags_nested_copies_and_skips_other_major_lines() -> None:
    lock = {
        "packages": {
            "": {"version": "0.0.0"},
            "node_modules/fast-uri": {"version": "3.1.4"},
            "node_modules/ajv/node_modules/fast-uri": {"version": "3.1.8"},
            "node_modules/fast-uri-extra": {"version": "0.0.1"},
            "node_modules/js-yaml": {"version": "4.3.0"},
            "node_modules/old/node_modules/js-yaml": {"version": "3.14.1"},
            "node_modules/source-map-js": {"version": "1.2.2"},
            "node_modules/brace-expansion": {"version": "5.0.8"},
            "node_modules/glob/node_modules/brace-expansion": {"version": "2.0.1"},
            "node_modules/postcss": {"version": "8.5.23-beta.1"},
            "node_modules/css/node_modules/postcss": {"version": "8.5.19"},
            "node_modules/next/node_modules/postcss": {"version": "8.5.23+build.7"},
            "node_modules/postcss-selector-parser": {"version": "7.1.4"},
        }
    }

    assert violations(lock) == [
        "node_modules/fast-uri 3.1.4 < 3.1.9",
        "node_modules/ajv/node_modules/fast-uri 3.1.8 < 3.1.9",
        "node_modules/js-yaml 4.3.0 < 4.3.2",
        "node_modules/brace-expansion 5.0.8 < 5.0.12",
        "node_modules/postcss 8.5.23-beta.1 < 8.5.23",
        "node_modules/css/node_modules/postcss 8.5.19 < 8.5.23",
        "node_modules/postcss-selector-parser 7.1.4 < 7.1.6",
    ]


def test_req_deps_1_unparseable_version_fails_loudly() -> None:
    with pytest.raises(ValueError, match="unparseable"):
        parse("latest")
    with pytest.raises(ValueError, match="unparseable"):
        parse("^3.1.9")
