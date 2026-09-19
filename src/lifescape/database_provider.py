"""Database-backed evidence retrieval for local research packets.

This module owns a local database as an evidence source for research packets.
It provides an optional fallback layer when remote connectors are unavailable 
or to reduce redundant API calls by caching previously fetched observations.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

from lifescape.db import get_observation, save_observations
from lifescape.models import ObservationRecord, StrictModel
from lifescape.research_sources import EvidenceFetchResult, ResearchEvidenceProvider
from lifescape.research import ResearchPacket


class DatabaseEvidenceProvider(ResearchEvidenceProvider):
    """Provides evidence from a local SQLite database with fallback to remote sources."""
    
    def __init__(
        self,
        *,
        db_path: Path | str,
        cache_enabled: bool = True,
        cache_ttl_hours: int = 24,
        remote_provider: ResearchEvidenceProvider | None = None,
    ) -> None:
        """
        Initialize database evidence provider.
        
        Args:
            db_path: Path to SQLite database file
            cache_enabled: Whether to use caching
            cache_ttl_hours: Cache time-to-live in hours
            remote_provider: Fallback remote provider for missing data
        """
        self.db_path = Path(db_path)
        self.cache_enabled = cache_enabled
        self.cache_ttl_hours = cache_ttl_hours
        self.remote_provider = remote_provider
        
        # Initialize the database if it doesn't exist
        from lifescape.db import initialize_database
        initialize_database(self.db_path)
        
    def fetch(self, packet: ResearchPacket) -> EvidenceFetchResult:
        """
        Fetch evidence for research packet leads.
        
        First tries to retrieve from local database, then falls back to 
        remote provider if needed.
        """
        observations = []
        errors = {}
        
        # For each lead in the packet
        for lead in packet.leads:
            place_id = lead.place.place_id
            
            # Try to get existing observations from local DB
            try:
                # Note: In a real implementation, you'd query the DB for 
                # actual stored observations for this place and related metrics
                pass  # Placeholder - the database access would go here
                
            except Exception as e:
                # Log error but continue with fallback
                errors.setdefault(place_id, []).append(
                    f"database fetch failed: {str(e)}"
                )
            
        # If we have a remote provider and no successful local fetch,
        # try the remote fallback
        if not observations and self.remote_provider:
            return self.remote_provider.fetch(packet)
            
        return EvidenceFetchResult(observations=tuple(observations), errors=errors)


# Convenience function to create database provider with environment configuration
def create_database_provider() -> DatabaseEvidenceProvider | None:
    """Create a database evidence provider from environment variables."""
    
    # Check if database is enabled in environment
    db_enabled = os.environ.get("LIFESCAPE_LOCAL_DB_ENABLED", "").lower() == "true"
    if not db_enabled:
        return None
        
    db_path = os.environ.get("LIFESCAPE_LOCAL_DB_PATH", "local_evidence.db")
    cache_enabled = os.environ.get("LIFESCAPE_LOCAL_DB_CACHE", "true").lower() != "false"
    cache_ttl = int(os.environ.get("LIFESCAPE_LOCAL_DB_CACHE_TTL_HOURS", "24"))
    
    return DatabaseEvidenceProvider(
        db_path=db_path,
        cache_enabled=cache_enabled,
        cache_ttl_hours=cache_ttl
    )


def create_combined_provider() -> ResearchEvidenceProvider:
    """
    Create a combined evidence provider that uses both database and remote sources.
    
    This implementation shows how you can combine local database storage with 
    remote fetchers to create an efficient evidence collection system.
    """
    # Create the database-based provider
    db_provider = create_database_provider()
    
    # Create the standard connector-based provider (fallback)
    connector_provider = ConnectorEvidenceProvider()
    
    # If we have a database provider, use it as primary with fallback
    if db_provider:
        return DatabaseEvidenceProvider(
            db_path=db_provider.db_path,
            cache_enabled=db_provider.cache_enabled,
            remote_provider=connector_provider
        )
    else:
        return connector_provider


# Placeholder class - this would be the original implementation  
class ConnectorEvidenceProvider(ResearchEvidenceProvider):
    """Placeholder for the original connector-based provider."""
    
    def fetch(self, packet: ResearchPacket) -> EvidenceFetchResult:
        """
        This is a placeholder showing how the original provider worked.
        In practice, this would fetch from remote sources.
        """
        # Original implementation goes here
        return EvidenceFetchResult()