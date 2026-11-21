"""`docs/api-inventory.md` documents the hosted MCP and OAuth routes (BUI-2040)."""

from __future__ import annotations

import re
from pathlib import Path

from starlette.routing import Route

from lifescape.mcp_http import HOSTED_ORIGIN, MCP_PATH, OWNER_PATHS, create_owner_http_app
from lifescape.oauth import (
    GITHUB_CLIENT_ID_ENV,
    GITHUB_CLIENT_SECRET_ENV,
    OWNER_ENV,
    SIGNING_KEY_ENV,
)
from lifescape.store import DATABASE_URL_ENV, STORE_ENV

INVENTORY = Path(__file__).resolve().parents[1] / "docs" / "api-inventory.md"
HOSTED_HEADING = "## Hosted site routes"
ROUTE_CELL = re.compile(r"^`(?:(?P<methods>[A-Z]+(?:, [A-Z]+)*) )?(?P<path>/[^`\s]*)`$")


def _section(heading: str) -> str:
    text = INVENTORY.read_text(encoding="utf-8")
    assert heading in text, f"{INVENTORY.name} has no {heading!r} section"
    body = text.split(heading, 1)[1]
    return body.split("\n## ", 1)[0]


def _documented_routes() -> dict[str, set[str]]:
    """Route path -> documented methods (empty when the row names no method)."""
    routes: dict[str, set[str]] = {}
    for line in _section(HOSTED_HEADING).splitlines():
        if not line.startswith("| `"):
            continue
        cell = line.split("|")[1].strip()
        match = ROUTE_CELL.match(cell)
        assert match, f"hosted route cell is not `[METHODS ]/path`: {cell!r}"
        path = match["path"]
        assert path not in routes, f"{path} is documented twice"
        methods = match["methods"]
        routes[path] = set(methods.split(", ")) if methods else set()
    return routes


def _owner_routes(tmp_path: Path) -> dict[str, set[str]]:
    env = {
        OWNER_ENV: "lifescape-owner",
        GITHUB_CLIENT_ID_ENV: "gh-client-id",
        GITHUB_CLIENT_SECRET_ENV: "gh-client-value-never-shown",
        SIGNING_KEY_ENV: "s" * 48,
        STORE_ENV: "database",
        DATABASE_URL_ENV: f"sqlite:///{tmp_path / 'hosted.sqlite'}",
    }
    app = create_owner_http_app(output_dir=tmp_path, base_url=HOSTED_ORIGIN, env=env)
    routes: dict[str, set[str]] = {}
    for route in app.routes:
        assert isinstance(route, Route)
        routes[route.path] = set(route.methods or ()) - {"HEAD", "OPTIONS"}
    return routes


def test_inventory_documents_exactly_the_hosted_owner_paths() -> None:
    assert set(_documented_routes()) == set(OWNER_PATHS)


def test_inventory_methods_match_the_owner_app_routes(tmp_path: Path) -> None:
    documented = _documented_routes()
    served = _owner_routes(tmp_path)
    for path, methods in documented.items():
        if path == MCP_PATH:
            # The MCP SDK owns the transport's methods; the row names none.
            assert methods == set(), f"{path} row should not name methods"
            continue
        assert methods == served[path], path


def test_inventory_no_longer_claims_every_route_404s_when_hosted() -> None:
    text = INVENTORY.read_text(encoding="utf-8")
    assert "All routes return 404 when `hosted_demo=True`." not in text
    local = _section("## Local application routes")
    assert "every route above answers 404" in local
    assert "every other `/api/` path that the owner app does not serve" in local


def test_inventory_documents_the_unconfigured_503() -> None:
    hosted = _section(HOSTED_HEADING)
    assert "503" in hosted
    assert "`not_configured`" in hosted
    assert "names, never values" in hosted
