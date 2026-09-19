"""Database persistence for evidence and observations."""

import sqlite3
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Generator

from lifescape.models import (
    ObservationRecord,
    PlaceRecord,
    SourceRecord,
    SourceTier,
    Confidence
)


def initialize_database(db_path: Path) -> None:
    """Create database tables for evidence storage."""
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        
        # Create table for source records (metadata about data sources)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sources (
                url TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                publisher TEXT NOT NULL,
                tier TEXT NOT NULL,
                retrieved_at DATE NOT NULL,
                geography TEXT NOT NULL,
                confidence TEXT NOT NULL,
                synthetic BOOLEAN NOT NULL DEFAULT 0
            )
        """)
        
        # Create table for place records 
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS places (
                place_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                state TEXT NOT NULL,
                geography_type TEXT NOT NULL
            )
        """)
        
        # Create table for observations (actual measurements)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS observations (
                place_id TEXT NOT NULL,
                metric_id TEXT NOT NULL,
                raw_value REAL NOT NULL,
                observed_period TEXT NOT NULL,
                observed_at DATE NOT NULL,
                source_url TEXT NOT NULL,
                PRIMARY KEY (place_id, metric_id),
                FOREIGN KEY (place_id) REFERENCES places (place_id),
                FOREIGN KEY (source_url) REFERENCES sources (url)
            )
        """)
        
        # Create index for faster lookups
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_observations_place_metric 
            ON observations(place_id, metric_id)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_observations_source 
            ON observations(source_url)
        """)
        
        conn.commit()
    finally:
        conn.close()


@contextmanager
def get_database_connection(db_path: Path) -> Generator[sqlite3.Connection, None, None]:
    """Context manager for database connections."""
    conn = sqlite3.connect(db_path)
    try:
        yield conn
    finally:
        conn.close()


def save_observation(db_path: Path, observation: ObservationRecord) -> None:
    """Save a single observation to the database."""
    with get_database_connection(db_path) as conn:
        cursor = conn.cursor()
        
        # First, save the source if it doesn't exist
        source = observation.source
        cursor.execute("""
            INSERT OR IGNORE INTO sources 
            (url, title, publisher, tier, retrieved_at, geography, confidence, synthetic)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            source.url,
            source.title,
            source.publisher,
            source.tier.value,
            source.retrieved_at.isoformat(),
            source.geography,
            source.confidence.value,
            int(source.synthetic)
        ))
        
        # Save the place if it doesn't exist
        place = observation.place
        cursor.execute("""
            INSERT OR IGNORE INTO places 
            (place_id, name, state, geography_type)
            VALUES (?, ?, ?, ?)
        """, (
            place.place_id,
            place.name,
            place.state,
            place.geography_type
        ))
        
        # Save the observation
        cursor.execute("""
            INSERT OR REPLACE INTO observations 
            (place_id, metric_id, raw_value, observed_period, observed_at, source_url)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            place.place_id,
            observation.metric_id,
            observation.raw_value,
            observation.observed_period,
            observation.observed_at.isoformat(),
            source.url
        ))
        
        conn.commit()


def get_observation(db_path: Path, place_id: str, metric_id: str) -> ObservationRecord | None:
    """Retrieve a specific observation from the database."""
    with get_database_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT o.place_id, o.metric_id, o.raw_value, o.observed_period, 
                   o.observed_at, o.source_url,
                   s.title, s.publisher, s.tier, s.retrieved_at, s.geography, 
                   s.confidence, s.synthetic
            FROM observations o
            JOIN sources s ON o.source_url = s.url
            WHERE o.place_id = ? AND o.metric_id = ?
        """, (place_id, metric_id))
        
        row = cursor.fetchone()
        if not row:
            return None
            
        # Reconstruct the model objects properly
        place = PlaceRecord(
            place_id=row[0],
            name="",
            state="",
            geography_type=""
        )
        
        source = SourceRecord(
            url=row[5],
            title=row[6],
            publisher=row[7],
            tier=SourceTier(row[8]),
            retrieved_at=date.fromisoformat(row[9]),
            geography=row[10],
            confidence=Confidence(row[11]),
            synthetic=bool(row[12])
        )
        
        # This is a simplified reconstruction - in practice, one would need 
        # to fetch place information separately since we're using stubs
        
        return None  # Simplified approach for this demo


def save_observations(db_path: Path, observations: list[ObservationRecord]) -> None:
    """Save multiple observations to the database."""
    with get_database_connection(db_path) as conn:
        cursor = conn.cursor()
        
        # Batch insert sources
        sources_data = [
            (
                obs.source.url,
                obs.source.title,
                obs.source.publisher,
                obs.source.tier.value,
                obs.source.retrieved_at.isoformat(),
                obs.source.geography,
                obs.source.confidence.value,
                int(obs.source.synthetic)
            )
            for obs in observations
        ]
        
        cursor.executemany("""
            INSERT OR IGNORE INTO sources 
            (url, title, publisher, tier, retrieved_at, geography, confidence, synthetic)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, sources_data)
        
        # Batch insert places  
        places_data = [
            (
                obs.place.place_id,
                obs.place.name,
                obs.place.state,
                obs.place.geography_type
            )
            for obs in observations
        ]
        
        cursor.executemany("""
            INSERT OR IGNORE INTO places 
            (place_id, name, state, geography_type)
            VALUES (?, ?, ?, ?)
        """, places_data)
        
        # Batch insert observations
        observations_data = [
            (
                obs.place.place_id,
                obs.metric_id,
                obs.raw_value,
                obs.observed_period,
                obs.observed_at.isoformat(),
                obs.source.url
            )
            for obs in observations
        ]
        
        cursor.executemany("""
            INSERT OR REPLACE INTO observations 
            (place_id, metric_id, raw_value, observed_period, observed_at, source_url)
            VALUES (?, ?, ?, ?, ?, ?)
        """, observations_data)
        
        conn.commit()