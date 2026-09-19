"""Caching layer for evidence to avoid redundant API calls."""

import hashlib
import json
import pickle
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, TypeVar

from lifescape.db import get_database_connection

T = TypeVar('T')

class EvidenceCache:
    """Cache for managing local evidence to reduce API calls."""
    
    def __init__(self, cache_dir: Path, ttl_hours: int = 24):
        self.cache_dir = cache_dir
        self.ttl_hours = ttl_hours
        self.cache_dir.mkdir(exist_ok=True)
        
    def _get_cache_key(self, func_name: str, args: tuple, kwargs: dict) -> str:
        """Generate a cache key based on function name and arguments."""
        key_string = f"{func_name}:{str(args)}:{str(sorted(kwargs.items()))}"
        return hashlib.md5(key_string.encode()).hexdigest()
        
    def get(self, func_name: str, args: tuple, kwargs: dict) -> Any | None:
        """Retrieve a cached item."""
        cache_key = self._get_cache_key(func_name, args, kwargs)
        cache_file = self.cache_dir / f"{cache_key}.pkl"
        
        if not cache_file.exists():
            return None
            
        # Check if cache is still valid
        file_time = datetime.fromtimestamp(cache_file.stat().st_mtime)
        if datetime.now() - file_time > timedelta(hours=self.ttl_hours):
            # Cache expired, remove it
            cache_file.unlink()
            return None
            
        try:
            with open(cache_file, 'rb') as f:
                return pickle.load(f)
        except (EOFError, FileNotFoundError, pickle.UnpicklingError):
            # Cache corrupted or unreadable, remove it
            cache_file.unlink()
            return None
            
    def set(self, func_name: str, args: tuple, kwargs: dict, value: Any) -> None:
        """Store an item in the cache."""
        cache_key = self._get_cache_key(func_name, args, kwargs)
        cache_file = self.cache_dir / f"{cache_key}.pkl"
        
        try:
            with open(cache_file, 'wb') as f:
                pickle.dump(value, f)
        except Exception:
            # If caching fails, we don't want to crash the application
            pass

# Global cache instance
cache = EvidenceCache(Path("cache"))