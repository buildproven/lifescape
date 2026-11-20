"""Vercel entry point: the public Lifescape site and its hosted MCP at ``/api/mcp``."""

from pathlib import Path

from lifescape.web import create_hosted_site

app = create_hosted_site(Path("/tmp/lifescape"))
