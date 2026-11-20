from __future__ import annotations

import os
import subprocess
import sys
from datetime import date

import pytest

from lifescape.models import (
    Confidence,
    ObservationRecord,
    PlaceRecord,
    SourceRecord,
    SourcesConfig,
    SourceTier,
)

# Release-budget tests measure CPU time. Parallel workers share cores and caches, which
# inflates CPU time (4.4 s against a 3 s budget on a 4-worker CI run, BUI-1804), so these
# tests run alone: workers skip them and the controller runs them serially afterwards.
BUDGET_PREFIX = "test_release_budget"
BUDGET_MARK = "release_budget"
SERIAL_PASS = "LIFESCAPE_RELEASE_BUDGET_PASS"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", f"{BUDGET_MARK}: a CPU-time budget that must run alone")


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    budget = [item for item in items if item.name.startswith(BUDGET_PREFIX)]
    for item in budget:
        item.add_marker(BUDGET_MARK)
    if budget and "PYTEST_XDIST_WORKER" in os.environ:
        config.hook.pytest_deselected(items=budget)
        items[:] = [item for item in items if item not in budget]


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    config = session.config
    if (
        "PYTEST_XDIST_WORKER" in os.environ
        or SERIAL_PASS in os.environ
        or not config.pluginmanager.has_plugin("dsession")
    ):
        return
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-o",
            "addopts=--strict-markers --strict-config",
            "-p",
            "no:cacheprovider",
            "-q",
            "-m",
            BUDGET_MARK,
            *config.args,
        ],
        cwd=config.rootpath,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, SERIAL_PASS: "1"},
    )
    if result.returncode == pytest.ExitCode.NO_TESTS_COLLECTED:
        return
    reporter = config.pluginmanager.get_plugin("terminalreporter")
    summary = (result.stdout.strip().splitlines() or ["no output"])[-1]
    if reporter is not None:
        reporter.write_line(f"release-budget tests run serially: {summary}")
        if result.returncode != pytest.ExitCode.OK:
            reporter.write_line(result.stdout + result.stderr)
    if result.returncode != pytest.ExitCode.OK:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
    elif session.exitstatus == pytest.ExitCode.NO_TESTS_COLLECTED:
        # Every collected test was a budget test, and the serial pass ran them.
        session.exitstatus = pytest.ExitCode.OK


@pytest.fixture
def policy() -> SourcesConfig:
    return SourcesConfig(
        allowed_scoring_tiers=frozenset({SourceTier.A, SourceTier.B}),
        discovery_only_tiers=frozenset({SourceTier.C}),
        minimum_gate_confidence=Confidence.HIGH,
        max_age_days=730,
    )


@pytest.fixture
def observation_factory():
    def make(
        place_id: str,
        metric_id: str,
        value: float,
        *,
        tier: SourceTier = SourceTier.A,
        confidence: Confidence = Confidence.HIGH,
    ) -> ObservationRecord:
        return ObservationRecord(
            place=PlaceRecord(place_id=place_id, name=place_id.title(), state="NC"),
            metric_id=metric_id,
            raw_value=value,
            observed_period="2025",
            observed_at=date(2025, 12, 31),
            source=SourceRecord(
                url=f"https://example.gov/{place_id}/{metric_id}",
                title="Evidence",
                publisher="Official source",
                tier=tier,
                retrieved_at=date(2026, 1, 1),
                geography="town",
                confidence=confidence,
                synthetic=True,
            ),
        )

    return make
