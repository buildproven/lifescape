from __future__ import annotations

from collections import Counter
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


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    """Name each test file a distributed run ran (BUI-1490, BUI-1804).

    Serial progress output names every file; pytest-xdist prints only dots at verbosity 0,
    and Keel reads test file names from the suite output.
    """
    if terminalreporter.verbosity > 0:
        return
    if not terminalreporter.config.pluginmanager.has_plugin("dsession"):
        return
    outcomes: dict[str, Counter[str]] = {}
    for reports in terminalreporter.stats.values():
        for report in reports:
            if isinstance(report, pytest.TestReport) and (
                report.when == "call" or not report.passed
            ):
                path = report.nodeid.split("::", 1)[0]
                outcomes.setdefault(path, Counter())[report.outcome] += 1
    if not outcomes:
        return
    terminalreporter.write_sep("-", "test files")
    for path in sorted(outcomes):
        counts = ", ".join(f"{n} {outcome}" for outcome, n in sorted(outcomes[path].items()))
        terminalreporter.write_line(f"{path} {counts}")


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
