"""Locked dev dependencies stay outside known advisories that fail pip-audit."""

from __future__ import annotations

from importlib.metadata import version

import pytest

# filelock CVE-2026-1129xx (fixed 4.2.1); msgpack CVE-2026-114613 (fixed 1.2.3).
FIRST_PATCHED = {"filelock": (4, 2, 1), "msgpack": (1, 2, 3)}


def parse(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("+")[0].split(".")[:3])


@pytest.mark.parametrize("package", sorted(FIRST_PATCHED))
def test_installed_release_is_patched(package: str) -> None:
    assert parse(version(package)) >= FIRST_PATCHED[package]


def test_last_vulnerable_release_is_rejected() -> None:
    assert parse("3.30.0") < FIRST_PATCHED["filelock"]
    assert parse("1.2.1") < FIRST_PATCHED["msgpack"]
