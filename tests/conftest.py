from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterable, Sequence
from datetime import date
from typing import Any

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

# At verbosity 0 xdist prints only dots, so Keel's red proof (which looks for each new test file's
# name in the output) needed `-v`: one line per test, ~1,200 per run (BUI-2026). Instead, the run
# prints every test file it collected on one line, before the tests run, so failure details stay at
# the end of the output, where Keel reads its tail.
TEST_FILES_PREFIX = "test files"


def files_line(nodeids: Iterable[str]) -> str | None:
    """One line naming each test file of `nodeids` once, in collection order."""
    files = list(dict.fromkeys(nodeid.split("::", 1)[0] for nodeid in nodeids))
    if not files:
        return None
    return f"{TEST_FILES_PREFIX} ({len(files)}): {' '.join(files)}"


class FileListReporter:
    """Writes the test-file line once per run: from the xdist controller, else after collection."""

    def __init__(self, config: pytest.Config) -> None:
        self.config = config
        self.written = False

    def write(self, nodeids: Iterable[str]) -> None:
        line = files_line(nodeids)
        reporter = self.config.pluginmanager.get_plugin("terminalreporter")
        if self.written or line is None or reporter is None:
            return
        self.written = True
        reporter.ensure_newline()
        reporter.write_line(line)

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        self.write(item.nodeid for item in session.items)

    @pytest.hookimpl(optionalhook=True)
    def pytest_xdist_node_collection_finished(self, node: Any, ids: Sequence[str]) -> None:
        # Every worker collects the same ids (xdist refuses to run otherwise); write them once.
        self.write(ids)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", f"{BUDGET_MARK}: a CPU-time budget that must run alone")
    if "PYTEST_XDIST_WORKER" not in os.environ:
        config.pluginmanager.register(FileListReporter(config), "lifescape-test-file-list")


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
    lines = result.stdout.strip().splitlines()
    summary = (lines or ["no output"])[-1]
    if reporter is not None:
        reporter.write_line(f"release-budget tests run serially: {summary}")
        # Workers deselect budget tests, so a file holding only budget tests is named by this pass.
        files = [line for line in lines if line.startswith(f"{TEST_FILES_PREFIX} (")]
        if files:
            reporter.write_line(f"release-budget {files[0]}")
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
