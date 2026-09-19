#!/usr/bin/env python3
"""Example of using local database for evidence storage."""

from pathlib import Path
from lifescape.db import initialize_database, save_observations
from lifescape.models import (
    PlaceRecord,
    SourceRecord,
    SourceTier,
    Confidence,
    ObservationRecord,
    MetricDefinition,
)
from datetime import date

def main():
    # Initialize a test database
    db_path = Path("example_evidence.db")
    initialize_database(db_path)
    
    # Create some sample observations 
    place = PlaceRecord(
        place_id="test_place_1",
        name="Test Town",
        state="NC",
        geography_type="town"
    )
    
    source = SourceRecord(
        url="https://example.com/test-source",
        title="Test Data Source",
        publisher="Test Publisher", 
        tier=SourceTier.A,
        retrieved_at=date(2023, 6, 1),
        geography="Test Geography",
        confidence=Confidence.HIGH,
        synthetic=False
    )
    
    observation = ObservationRecord(
        place=place,
        metric_id="population",
        raw_value=50000.0,
        observed_period="2022-2023",
        observed_at=date(2023, 6, 1),
        source=source
    )
    
    # Save to database
    save_observations(db_path, [observation])
    
    print("Example database evidence successfully created!")
    print(f"Database location: {db_path}")

if __name__ == "__main__":
    main()