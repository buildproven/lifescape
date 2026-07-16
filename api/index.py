"""Vercel entry point for the static synthetic Lifescape demonstration."""

from pathlib import Path

from retirement_engine.web import create_app

app = create_app(Path("/tmp/lifescape"), hosted_demo=True)
