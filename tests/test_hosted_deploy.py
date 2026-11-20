"""BUI-2010: lifescape.buildproven.ai serves the hosted MCP at /api/mcp.

The Vercel entry (``api/index.py``) is loaded the way the platform loads it, with the process
environment set by each test, and driven through Starlette's ``TestClient`` (which runs the
lifespan). No test connects to a database or reaches GitHub.
"""

from __future__ import annotations

import runpy
import tomllib
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from packaging.requirements import Requirement
from starlette.types import ASGIApp

from lifescape.mcp_http import HOSTED_ORIGIN, MCP_PATH, OWNER_PATHS, create_owner_http_app
from lifescape.oauth import (
    GITHUB_CLIENT_ID_ENV,
    GITHUB_CLIENT_SECRET_ENV,
    OWNER_ENV,
    REQUIRED_ENV,
    SIGNING_KEY_ENV,
    OAuthSettings,
    OwnerAuthorizationServer,
)
from lifescape.store import DATABASE_URL_ENV, STORE_ENV, StoreConfigError, store_from_env
from lifescape.store_db import DatabaseStore
from lifescape.web import create_app

REPOSITORY = Path(__file__).resolve().parents[1]
SIGNING_KEY = "s" * 48
CLIENT_VALUE = "gh-client-value-never-shown"
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1"},
    },
}
JSON_HEADERS = {"Accept": "application/json, text/event-stream"}
HOSTED_NAMES = (*REQUIRED_ENV, STORE_ENV, DATABASE_URL_ENV)
BOUNDARY_404 = {"detail": "the hosted site has no application API"}


def _env(tmp_path: Path) -> dict[str, str]:
    return {
        OWNER_ENV: "lifescape-owner",
        GITHUB_CLIENT_ID_ENV: "gh-client-id",
        GITHUB_CLIENT_SECRET_ENV: CLIENT_VALUE,
        SIGNING_KEY_ENV: SIGNING_KEY,
        STORE_ENV: "database",
        DATABASE_URL_ENV: f"sqlite:///{tmp_path / 'hosted.sqlite'}",
    }


def _entry(monkeypatch: pytest.MonkeyPatch, env: Mapping[str, str]) -> ASGIApp:
    for name in (*HOSTED_NAMES, "LIFESCAPE_OWNER_GITHUB", "LIFESCAPE_DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    app: ASGIApp = runpy.run_path(str(REPOSITORY / "api" / "index.py"))["app"]
    return app


@pytest.fixture
def client_for(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    clients: list[TestClient] = []

    def build(env: Mapping[str, str]) -> TestClient:
        client = TestClient(_entry(monkeypatch, env), base_url=HOSTED_ORIGIN)
        client.__enter__()
        clients.append(client)
        return client

    yield build
    for client in clients:
        client.__exit__(None, None, None)


def _assert_pages_serve(client: TestClient) -> None:
    landing = client.get("/")
    demo = client.get("/demo")
    assert landing.status_code == 200
    assert "Decide where retirement still works." in landing.text
    assert demo.status_code == 200
    assert "Williamsburg leads this field." in demo.text


# ------------------------------------------------------------------ REQ-DEPLOY-1


def test_req_deploy_1_hosted_mcp_answers_401_with_resource_metadata(
    tmp_path: Path, client_for: Any
) -> None:
    client = client_for(_env(tmp_path))

    response = client.post("/api/mcp", json=INITIALIZE, headers=JSON_HEADERS)

    assert MCP_PATH == "/api/mcp"
    assert response.status_code == 401
    challenge = response.headers["www-authenticate"]
    assert challenge.startswith("Bearer ")
    assert f'resource_metadata="{HOSTED_ORIGIN}/.well-known/oauth-protected-resource"' in challenge


def test_req_deploy_1_well_known_documents_name_the_hosted_origin(
    tmp_path: Path, client_for: Any
) -> None:
    client = client_for(_env(tmp_path))

    resource = client.get("/.well-known/oauth-protected-resource")
    server = client.get("/.well-known/oauth-authorization-server")

    assert resource.status_code == 200
    assert resource.json()["authorization_servers"] == [HOSTED_ORIGIN]
    assert server.status_code == 200
    metadata = server.json()
    assert metadata["issuer"] == HOSTED_ORIGIN
    assert metadata["authorization_endpoint"] == f"{HOSTED_ORIGIN}/authorize"
    assert metadata["token_endpoint"] == f"{HOSTED_ORIGIN}/token"
    assert metadata["registration_endpoint"] == f"{HOSTED_ORIGIN}/register"


def test_req_deploy_1_oauth_endpoints_reach_the_authorization_server(
    tmp_path: Path, client_for: Any
) -> None:
    client = client_for(_env(tmp_path))

    registered = client.post(
        "/register",
        json={
            "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
        },
    )
    token = client.post("/token", data={"grant_type": "authorization_code"})
    authorize = client.get("/authorize")
    callback = client.get("/oauth/github/callback")

    assert registered.status_code == 201, registered.text
    assert registered.json()["client_id"]
    assert token.status_code in (400, 401)
    assert "error" in token.json()
    assert authorize.status_code == 400
    assert authorize.json()["error"] == "invalid_request"
    assert callback.status_code == 400
    assert "Sign-in refused" in callback.text
    # A path the owner app routes with a method it refuses is its answer, not the site's.
    assert client.get("/token").status_code == 405


def test_req_deploy_1_owner_paths_are_exactly_the_owner_app_routes(tmp_path: Path) -> None:
    app = create_owner_http_app(output_dir=tmp_path, base_url=HOSTED_ORIGIN, env=_env(tmp_path))
    paths = {getattr(route, "path", None) for route in app.routes}
    assert paths == set(OWNER_PATHS)


def test_req_deploy_1_pages_serve_and_other_api_paths_answer_404(
    tmp_path: Path, client_for: Any
) -> None:
    client = client_for(_env(tmp_path))

    _assert_pages_serve(client)
    for method, path in (
        ("GET", "/api/bootstrap"),
        ("POST", "/api/run"),
        ("GET", "/api/mcp/"),
        ("GET", "/api/mcpx"),
        ("GET", "/openapi.json"),
    ):
        response = client.request(method, path)
        assert response.status_code == 404, path
        assert response.json() == BOUNDARY_404, path
    assert client.post("/mcp", json=INITIALIZE, headers=JSON_HEADERS).status_code == 404
    assert not (tmp_path / "hosted.sqlite").exists()


def test_req_deploy_1_signed_in_owner_reaches_the_tools_through_the_site(
    tmp_path: Path, client_for: Any
) -> None:
    """The site's lifespan starts the MCP session manager, so a valid token is served."""
    env = _env(tmp_path)
    client = client_for(env)
    issuer = OwnerAuthorizationServer(
        OAuthSettings.from_env(env), issuer=HOSTED_ORIGIN, resource=HOSTED_ORIGIN
    )
    token = issuer._issue("claude-client", []).access_token

    response = client.post(
        "/api/mcp",
        json=INITIALIZE,
        headers={**JSON_HEADERS, "Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["result"]["serverInfo"]["name"]


def test_req_deploy_1_wrong_host_is_refused_before_the_owner_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _entry(monkeypatch, _env(tmp_path))
    with TestClient(app, base_url="https://attacker.example") as client:
        response = client.get("/.well-known/oauth-authorization-server")
    assert response.status_code == 400


def test_req_deploy_1_owner_app_is_served_only_by_the_hosted_site(tmp_path: Path) -> None:
    owner = create_owner_http_app(output_dir=tmp_path, base_url=HOSTED_ORIGIN, env=_env(tmp_path))
    with pytest.raises(ValueError, match="hosted_demo=True"):
        create_app(tmp_path, hosted_mcp=owner)


# ------------------------------------------------------------------ REQ-DEPLOY-2


@pytest.mark.parametrize("name", HOSTED_NAMES)
@pytest.mark.parametrize("value", [None, "  "], ids=["unset", "blank"])
def test_req_deploy_2_missing_setting_answers_503_naming_it(
    tmp_path: Path, client_for: Any, name: str, value: str | None
) -> None:
    env = {key: val for key, val in _env(tmp_path).items() if key != name}
    if value is not None:
        env[name] = value
    client = client_for(env)

    response = client.post("/api/mcp", json=INITIALIZE, headers=JSON_HEADERS)

    assert response.status_code == 503
    body = response.json()
    assert body["missing"] == [name]
    assert body["invalid"] == []
    assert name in body["detail"]
    for secret in (CLIENT_VALUE, SIGNING_KEY, "gh-client-id", "lifescape-owner", str(tmp_path)):
        assert secret not in response.text
    _assert_pages_serve(client)


def test_req_deploy_2_every_missing_setting_is_named(client_for: Any) -> None:
    client = client_for({})

    for path in OWNER_PATHS:
        response = client.get(path)
        assert response.status_code == 503, path
        assert response.json()["missing"] == list(HOSTED_NAMES)
    _assert_pages_serve(client)
    assert client.get("/api/bootstrap").json() == BOUNDARY_404


@pytest.mark.parametrize(
    ("name", "value"),
    [
        (SIGNING_KEY_ENV, "too-short-key"),
        (OWNER_ENV, "not a login"),
        (STORE_ENV, "local"),
        (DATABASE_URL_ENV, "postgresql://user:hunter2@host:notaport/db"),
        (DATABASE_URL_ENV, "nosuchdialect://user:hunter2@host/db"),
    ],
)
def test_req_deploy_2_unusable_setting_answers_503_naming_it_without_its_value(
    tmp_path: Path, client_for: Any, name: str, value: str
) -> None:
    client = client_for({**_env(tmp_path), name: value})

    response = client.post("/api/mcp", json=INITIALIZE, headers=JSON_HEADERS)

    assert response.status_code == 503
    body = response.json()
    assert body["missing"] == []
    assert body["invalid"] == [name]
    assert value not in response.text
    assert "hunter2" not in response.text
    _assert_pages_serve(client)


# ------------------------------------------------------------------ REQ-DEPLOY-3


def test_req_deploy_3_settings_use_the_fleet_names() -> None:
    assert OWNER_ENV == "OWNER_GITHUB_LOGIN"
    assert GITHUB_CLIENT_ID_ENV == "LIFESCAPE_GITHUB_OAUTH_CLIENT_ID"
    assert GITHUB_CLIENT_SECRET_ENV == "LIFESCAPE_GITHUB_OAUTH_CLIENT_SECRET"
    assert SIGNING_KEY_ENV == "LIFESCAPE_OAUTH_SIGNING_KEY"
    assert DATABASE_URL_ENV == "DATABASE_URL"
    settings = OAuthSettings.from_env(
        {
            "OWNER_GITHUB_LOGIN": "the-owner",
            "LIFESCAPE_GITHUB_OAUTH_CLIENT_ID": "id",
            "LIFESCAPE_GITHUB_OAUTH_CLIENT_SECRET": "secret",
            "LIFESCAPE_OAUTH_SIGNING_KEY": SIGNING_KEY,
        }
    )
    assert (settings.owner_login, settings.github_client_id, settings.github_client_secret) == (
        "the-owner",
        "id",
        "secret",
    )


def test_req_deploy_3_old_names_are_not_read(tmp_path: Path, client_for: Any) -> None:
    old = {
        "LIFESCAPE_OWNER_GITHUB": "lifescape-owner",
        "LIFESCAPE_GITHUB_CLIENT_ID": "gh-client-id",
        "LIFESCAPE_GITHUB_CLIENT_SECRET": CLIENT_VALUE,
        SIGNING_KEY_ENV: SIGNING_KEY,
        STORE_ENV: "database",
        "LIFESCAPE_DATABASE_URL": f"sqlite:///{tmp_path / 'old.sqlite'}",
    }
    client = client_for(old)

    response = client.post("/api/mcp", json=INITIALIZE, headers=JSON_HEADERS)

    assert response.status_code == 503
    assert response.json()["missing"] == [
        OWNER_ENV,
        GITHUB_CLIENT_ID_ENV,
        GITHUB_CLIENT_SECRET_ENV,
        DATABASE_URL_ENV,
    ]
    with pytest.raises(StoreConfigError, match="needs DATABASE_URL"):
        store_from_env(tmp_path, {STORE_ENV: "database", "LIFESCAPE_DATABASE_URL": "sqlite://"})


# ------------------------------------------------------------------ REQ-DEPLOY-4


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://u@h/db",
        "postgres://u@h/db",
        "postgresql://u:pw@ep-x.us-east-2.aws.neon.tech/neondb?sslmode=require",
    ],
)
def test_req_deploy_4_postgres_urls_use_psycopg_3(tmp_path: Path, url: str) -> None:
    store = store_from_env(tmp_path, {STORE_ENV: "database", DATABASE_URL_ENV: url})

    assert isinstance(store, DatabaseStore)
    assert store._engine.dialect.name == "postgresql"
    assert store._engine.dialect.driver == "psycopg"
    assert store._engine.url.drivername == "postgresql+psycopg"
    assert "pw" not in store.label


def test_req_deploy_4_an_explicit_driver_is_kept(tmp_path: Path) -> None:
    """Only the bare schemes are routed to psycopg 3; a named driver stays the caller's choice."""
    with pytest.raises(StoreConfigError, match=r"postgresql\+psycopg2://u:\*\*\*@h/db"):
        store_from_env(
            tmp_path, {STORE_ENV: "database", DATABASE_URL_ENV: "postgresql+psycopg2://u:pw@h/db"}
        )


def test_req_deploy_4_pyproject_declares_psycopg_3() -> None:
    pyproject = tomllib.loads((REPOSITORY / "pyproject.toml").read_text(encoding="utf-8"))
    requirements = [Requirement(item) for item in pyproject["project"]["dependencies"]]
    psycopg = [item for item in requirements if item.name == "psycopg"]
    assert len(psycopg) == 1
    assert "binary" in psycopg[0].extras
    assert psycopg[0].specifier.contains("3.3.0")
    assert not psycopg[0].specifier.contains("4.0.0")
