"""Local SQLite store of fetched observations with their exact provenance.

Each observation is stored with its own place and source snapshot, so a
re-fetch never changes what an earlier read returned. Rows are only appended
(an identical row is stored once), so earlier evidence stays reproducible.
Each place also keeps its last fetch outcome, so a partial fetch and its
errors stay visible instead of looking complete.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path

from lifescape.models import Confidence, ObservationRecord, PlaceRecord, SourceRecord, SourceTier

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    place_id TEXT NOT NULL,
    place_name TEXT NOT NULL,
    place_state TEXT NOT NULL,
    place_geography_type TEXT NOT NULL,
    metric_id TEXT NOT NULL,
    raw_value REAL NOT NULL,
    observed_period TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_title TEXT NOT NULL,
    source_publisher TEXT NOT NULL,
    source_tier TEXT NOT NULL,
    source_retrieved_at TEXT NOT NULL,
    source_geography TEXT NOT NULL,
    source_confidence TEXT NOT NULL,
    source_synthetic INTEGER NOT NULL,
    UNIQUE (
        place_id, place_name, place_state, place_geography_type, metric_id, raw_value,
        observed_period, observed_at, source_url, source_title, source_publisher,
        source_tier, source_retrieved_at, source_geography, source_confidence,
        source_synthetic
    )
);
CREATE INDEX IF NOT EXISTS observations_by_place ON observations (place_id, metric_id, id);
CREATE TABLE IF NOT EXISTS place_fetches (
    place_id TEXT PRIMARY KEY,
    fetched_at TEXT NOT NULL,
    errors TEXT NOT NULL
);
"""

_COLUMNS = (
    "place_id, place_name, place_state, place_geography_type, metric_id, raw_value, "
    "observed_period, observed_at, source_url, source_title, source_publisher, source_tier, "
    "source_retrieved_at, source_geography, source_confidence, source_synthetic"
)


class EvidenceStoreError(RuntimeError):
    """The file is not an evidence store this version can read."""


@contextmanager
def _connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def initialize_evidence_store(db_path: Path) -> None:
    """Create the store, or check that an existing file is one this version reads."""
    with _connect(db_path) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        tables = conn.execute("SELECT count(*) FROM sqlite_master WHERE type = 'table'").fetchone()[
            0
        ]
        if version == 0 and tables:
            raise EvidenceStoreError(f"{db_path} is not a lifescape evidence store")
        if version not in (0, SCHEMA_VERSION):
            raise EvidenceStoreError(f"{db_path} has evidence store version {version}")
        conn.executescript(_SCHEMA)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def _row(observation: ObservationRecord) -> tuple[object, ...]:
    place, source = observation.place, observation.source
    return (
        place.place_id,
        place.name,
        place.state,
        place.geography_type,
        observation.metric_id,
        observation.raw_value,
        observation.observed_period,
        observation.observed_at.isoformat(),
        source.url,
        source.title,
        source.publisher,
        source.tier.value,
        source.retrieved_at.isoformat(),
        source.geography,
        source.confidence.value,
        int(source.synthetic),
    )


def _record(row: tuple[object, ...]) -> ObservationRecord:
    return ObservationRecord(
        place=PlaceRecord(
            place_id=str(row[0]), name=str(row[1]), state=str(row[2]), geography_type=str(row[3])
        ),
        metric_id=str(row[4]),
        raw_value=float(str(row[5])),
        observed_period=str(row[6]),
        observed_at=date.fromisoformat(str(row[7])),
        source=SourceRecord(
            url=str(row[8]),
            title=str(row[9]),
            publisher=str(row[10]),
            tier=SourceTier(str(row[11])),
            retrieved_at=date.fromisoformat(str(row[12])),
            geography=str(row[13]),
            confidence=Confidence(str(row[14])),
            synthetic=bool(row[15]),
        ),
    )


def save_observations(db_path: Path, observations: list[ObservationRecord]) -> None:
    """Append observations; an identical observation is stored once."""
    with _connect(db_path) as conn:
        conn.executemany(
            f"INSERT OR IGNORE INTO observations ({_COLUMNS}) VALUES ({', '.join('?' * 16)})",
            [_row(observation) for observation in observations],
        )


def save_observation(db_path: Path, observation: ObservationRecord) -> None:
    save_observations(db_path, [observation])


def _latest(
    conn: sqlite3.Connection, where: str, args: tuple[str, ...]
) -> list[tuple[object, ...]]:
    return conn.execute(
        f"SELECT {_COLUMNS} FROM observations WHERE id IN ("
        f"  SELECT max(id) FROM observations WHERE {where} GROUP BY place_id, metric_id"
        ") ORDER BY metric_id",
        args,
    ).fetchall()


def get_observation(db_path: Path, place_id: str, metric_id: str) -> ObservationRecord | None:
    """The most recently stored observation of a metric for a place, or None."""
    with _connect(db_path) as conn:
        rows = _latest(conn, "place_id = ? AND metric_id = ?", (place_id, metric_id))
    return _record(rows[0]) if rows else None


def observations_for_place(db_path: Path, place_id: str) -> tuple[ObservationRecord, ...]:
    """The most recently stored observation of each metric for a place, by metric."""
    with _connect(db_path) as conn:
        rows = _latest(conn, "place_id = ?", (place_id,))
    return tuple(_record(row) for row in rows)


def observation_history(
    db_path: Path, place_id: str, metric_id: str
) -> tuple[ObservationRecord, ...]:
    """Every stored observation of a metric for a place, oldest first."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT {_COLUMNS} FROM observations WHERE place_id = ? AND metric_id = ? ORDER BY id",
            (place_id, metric_id),
        ).fetchall()
    return tuple(_record(row) for row in rows)


def record_fetch(
    db_path: Path, place_id: str, errors: tuple[str, ...], fetched_at: datetime | None = None
) -> None:
    """Record the outcome of the latest remote fetch for a place."""
    when = (fetched_at or datetime.now(UTC)).isoformat()
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO place_fetches (place_id, fetched_at, errors) VALUES (?, ?, ?) "
            "ON CONFLICT (place_id) DO UPDATE SET fetched_at = excluded.fetched_at, "
            "errors = excluded.errors",
            (place_id, when, json.dumps(list(errors))),
        )


def last_fetch(db_path: Path, place_id: str) -> tuple[datetime, tuple[str, ...]] | None:
    """When a place was last fetched and the errors that fetch reported."""
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT fetched_at, errors FROM place_fetches WHERE place_id = ?", (place_id,)
        ).fetchone()
    if row is None:
        return None
    errors = json.loads(str(row[1]))
    if not isinstance(errors, list):
        raise EvidenceStoreError(f"fetch record for {place_id} is corrupt")
    return datetime.fromisoformat(str(row[0])), tuple(str(error) for error in errors)
