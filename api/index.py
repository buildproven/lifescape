"""Vercel entry point for the stateless synthetic Lifescape demo."""

import os
from pathlib import Path

from retirement_engine.web import create_app

app = create_app(
    Path("/tmp/lifescape"),
    hosted_demo=True,
    hosted_runs_enabled=os.getenv("LIFESCAPE_HOSTED_RUNS_ENABLED", "").lower() == "true",
)
