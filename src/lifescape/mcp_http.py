"""Hosted Lifescape MCP endpoint: Streamable HTTP behind an OAuth 2.0 resource server.

Agent-first surface (machine-setup ``docs/AGENT-FIRST.md``): chat on web and mobile reaches
Lifescape only over HTTP, so this app serves the same tools as the stdio server
(``lifescape.mcp_server.create_server``) at ``/api/mcp``, the path the fleet tools probe
(machine-setup ``scripts/plugin-connect.sh``, ``scripts/sweep.mjs``). It never issues tokens;
sign-in belongs to the authorization server named by ``issuer_url``.

- A request to ``/api/mcp`` without a valid bearer token gets 401 with
  ``WWW-Authenticate: Bearer resource_metadata="<origin>/.well-known/oauth-protected-resource"``.
- ``/.well-known/oauth-protected-resource`` serves RFC 9728 metadata naming the issuer. The
  resource identifier is the public origin: RFC 9728 §3.1 builds the metadata URL from the
  resource, so a root metadata path names the origin, and the metadata stays self-consistent
  for clients that check §3.3.
- A token counts only when ``token_verifier`` accepts it and reports it was issued for that
  resource (``AccessToken.resource``, the RFC 8707 resource indicator).

``create_owner_http_app`` is the hosted app: the same origin also serves the authorization
server (``lifescape.oauth``) that signs the owner in through GitHub and issues those tokens.
``create_hosted_mcp_app`` builds it from the deployment's environment for the public site
(``lifescape.web.create_hosted_site``); with a setting missing or unusable, every owner path
answers 503 naming the variables, so the site's pages still serve.

Two settings are optional: ``LIFESCAPE_SIGNUP`` (``lifescape.oauth``: ``open`` lets any GitHub
user sign in; unset, only the owner) and ``LIFESCAPE_DAILY_CALLS`` (each signed-in user's tool
calls per UTC day, 200 when unset; ``lifescape.mcp_server``). An unusable value is named in the
503 too (BUI-2212).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from pathlib import Path
from urllib.parse import SplitResult, urlsplit

import httpx2
from mcp.server.auth.provider import TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from lifescape.mcp_server import (
    DAILY_CALLS_ENV,
    ConnectorFactory,
    DailyCallsConfigError,
    create_server,
    daily_calls_from_env,
)
from lifescape.oauth import (
    AUTHORIZE_PATH,
    GITHUB_CALLBACK_PATH,
    METADATA_PATH,
    REGISTER_PATH,
    REQUIRED_ENV,
    TOKEN_PATH,
    OAuthConfigError,
    OAuthSettings,
    OwnerAuthorizationServer,
)
from lifescape.store import (
    DATABASE_STORE,
    DATABASE_URL_ENV,
    OWNER_LOGIN_ENV,
    STORE_ENV,
    Store,
    StoreConfigError,
    store_from_env,
)
from lifescape.store_db import DatabaseStore

MCP_PATH = "/api/mcp"
RESOURCE_METADATA_PATH = "/.well-known/oauth-protected-resource"
# The public origin. The GitHub OAuth app's callback is <origin>/oauth/github/callback.
HOSTED_ORIGIN = "https://lifescape.buildproven.ai"
# Every variable the hosted endpoint needs, in the order a 503 names them.
HOSTED_ENV = (*REQUIRED_ENV, STORE_ENV, DATABASE_URL_ENV)
# The paths the owner app serves; the public site hands exactly these to it.
OWNER_PATHS = (
    MCP_PATH,
    RESOURCE_METADATA_PATH,
    METADATA_PATH,
    AUTHORIZE_PATH,
    TOKEN_PATH,
    REGISTER_PATH,
    GITHUB_CALLBACK_PATH,
)
ALL_METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
LOGGER = logging.getLogger(__name__)
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
DEFAULT_PORTS = {"http": 80, "https": 443}


class HttpConfigError(ValueError):
    """A hosted-endpoint setting cannot serve OAuth safely."""


def _secure_url(value: str, label: str) -> tuple[SplitResult, str]:
    """Parse ``value``: an https URL, or http on a loopback host for local development.

    Returns the parts and the normalized netloc: lowercase host, IPv6 in brackets, and the port
    only when it is not the scheme default.
    """
    parts = urlsplit(value.strip())
    try:
        port = parts.port
    except ValueError as error:
        raise HttpConfigError(f"{label} has an invalid port: {value!r}") from error
    hostname = parts.hostname
    secure = parts.scheme == "https" or (parts.scheme == "http" and hostname in LOOPBACK_HOSTS)
    if not hostname or not secure:
        raise HttpConfigError(
            f"{label} must be an https URL (http only on a loopback host): {value!r}"
        )
    if parts.username is not None or parts.password is not None:
        raise HttpConfigError(f"{label} must not carry credentials: {value!r}")
    if parts.query or parts.fragment:
        raise HttpConfigError(f"{label} must not have a query or fragment: {value!r}")
    host = f"[{hostname}]" if ":" in hostname else hostname
    if port is None or port == DEFAULT_PORTS[parts.scheme]:
        return parts, host
    return parts, f"{host}:{port}"


def public_origin(base_url: str) -> str:
    """The public origin (``scheme://host[:port]``) the endpoint is reached at."""
    parts, netloc = _secure_url(base_url, "base_url")
    if parts.path not in ("", "/"):
        raise HttpConfigError(
            f"base_url must be an origin without a path (the endpoint is <origin>{MCP_PATH}): "
            f"{base_url!r}"
        )
    return f"{parts.scheme}://{netloc}"


def issuer_identifier(issuer_url: str) -> str:
    """The issuer without trailing slashes, so ``https://x/`` and ``https://x`` name one issuer."""
    _secure_url(issuer_url, "issuer_url")
    return issuer_url.strip().rstrip("/")


def create_http_app(
    *,
    output_dir: Path,
    base_url: str,
    issuer_url: str,
    token_verifier: TokenVerifier,
    config_dir: Path | None = None,
    store: Store | None = None,
    daily_calls: int | None = None,
    live_connectors: ConnectorFactory | None = None,
) -> Starlette:
    """Build the ASGI app for the hosted endpoint at ``<base_url>/api/mcp``.

    ``store`` keeps research packets and run report indexes; unset, ``LIFESCAPE_STORE`` in the
    process environment picks it (``lifescape.mcp_server.create_server``). Each signed-in user
    (the token ``sub``) gets ``daily_calls`` tool calls per UTC day; unset,
    ``LIFESCAPE_DAILY_CALLS`` in the process environment sets it. ``live_connectors`` replaces
    the public-data connectors (``create_server``); unset, the real ones serve.

    Run its lifespan (any ASGI server does) before serving requests: it starts the Streamable
    HTTP session manager. Sessions are stateless and answers are JSON, so any replica can serve
    any request.
    """
    origin = public_origin(base_url)
    # Validate through the model: its config keeps a path-less URL free of a trailing slash.
    auth = AuthSettings.model_validate(
        {
            "issuer_url": issuer_identifier(issuer_url),
            "resource_server_url": origin,
            "validate_token_resource": True,
        }
    )
    server = create_server(
        output_dir=output_dir,
        config_dir=config_dir,
        auth=auth,
        token_verifier=token_verifier,
        store=store,
        daily_calls=daily_calls,
        live_connectors=live_connectors,
    )
    return server.streamable_http_app(
        streamable_http_path=MCP_PATH,
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            allowed_hosts=[urlsplit(origin).netloc],
            allowed_origins=[origin],
        ),
    )


def create_owner_http_app(
    *,
    output_dir: Path,
    base_url: str,
    env: Mapping[str, str],
    config_dir: Path | None = None,
    github_transport: httpx2.AsyncBaseTransport | None = None,
    store: Store | None = None,
    live_connectors: ConnectorFactory | None = None,
) -> Starlette:
    """Build the hosted app: the MCP endpoint and its authorization server.

    Reads the OAuth settings from ``env`` first, so a missing variable stops the server at
    start (``OAuthConfigError`` names it); an unusable ``LIFESCAPE_DAILY_CALLS`` stops it too
    (``DailyCallsConfigError``). ``store`` unset, ``LIFESCAPE_STORE`` in ``env`` picks
    it. A database store also records spent codes and refresh tokens, so every replica on that
    database refuses a grant another replica exchanged (BUI-2027); the local store is one
    process, so they are recorded in it. ``github_transport`` and ``live_connectors`` replace
    the network for tests.
    """
    settings = OAuthSettings.from_env(env)
    daily_calls = daily_calls_from_env(env)
    origin = public_origin(base_url)
    records = store_from_env(output_dir, env) if store is None else store
    authorization = OwnerAuthorizationServer(
        settings,
        issuer=origin,
        resource=origin,
        grants=records if isinstance(records, DatabaseStore) else None,
        github_transport=github_transport,
    )
    app = create_http_app(
        output_dir=output_dir,
        base_url=origin,
        issuer_url=origin,
        token_verifier=authorization,
        config_dir=config_dir,
        store=records,
        daily_calls=daily_calls,
        live_connectors=live_connectors,
    )
    app.router.routes.extend(authorization.routes())
    return app


def _hosted_store(env: Mapping[str, str]) -> tuple[Store | None, list[str], list[str]]:
    """The hosted database store, or the store variables that are missing or unusable.

    The hosted server has no disk that outlives a request, so only the database store serves.
    Rows saved before they had an owner go to ``OWNER_GITHUB_LOGIN`` (BUI-2211).
    """
    kind = env.get(STORE_ENV, "").strip()
    url = env.get(DATABASE_URL_ENV, "").strip()
    missing = [name for name, value in ((STORE_ENV, kind), (DATABASE_URL_ENV, url)) if not value]
    invalid = [STORE_ENV] if kind and kind != DATABASE_STORE else []
    if missing or invalid:
        return None, missing, invalid
    try:
        owner = env.get(OWNER_LOGIN_ENV, "").strip() or None
        return DatabaseStore.from_url(url, owner=owner), [], []
    except StoreConfigError:
        # The message can name the URL's host and user; the 503 names the variable only.
        return None, [], [DATABASE_URL_ENV]


def _unconfigured_app(missing: Sequence[str], invalid: Sequence[str]) -> Starlette:
    """Every owner path answers 503 naming the variables to set or fix (names, never values)."""
    steps = []
    if missing:
        steps.append(f"set {', '.join(missing)}")
    if invalid:
        steps.append(f"fix {', '.join(invalid)}")
    detail = f"The hosted Lifescape MCP is not configured: {'; '.join(steps)}, then redeploy."
    body = {
        "error": "not_configured",
        "detail": detail,
        "missing": list(missing),
        "invalid": list(invalid),
    }
    LOGGER.error(detail)

    async def unavailable(request: Request) -> Response:
        return JSONResponse(body, status_code=503)

    return Starlette(routes=[Route(path, unavailable, methods=ALL_METHODS) for path in OWNER_PATHS])


def create_hosted_mcp_app(
    *,
    output_dir: Path,
    env: Mapping[str, str],
    base_url: str = HOSTED_ORIGIN,
    github_transport: httpx2.AsyncBaseTransport | None = None,
) -> Starlette:
    """The owner app for the public site, read from the deployment's ``env``.

    Every missing or unusable variable is named at once; any of them makes every owner path
    answer 503 instead (``_unconfigured_app``), so the site's pages still serve.
    """
    missing: list[str] = []
    invalid: list[str] = []
    try:
        OAuthSettings.from_env(env)
    except OAuthConfigError as error:
        missing.extend(error.missing)
        invalid.extend(error.invalid)
    try:
        daily_calls_from_env(env)
    except DailyCallsConfigError:
        invalid.append(DAILY_CALLS_ENV)
    store, store_missing, store_invalid = _hosted_store(env)
    missing.extend(store_missing)
    invalid.extend(store_invalid)
    if store is None or missing or invalid:
        return _unconfigured_app(missing, invalid)
    return create_owner_http_app(
        output_dir=output_dir,
        base_url=base_url,
        env=env,
        github_transport=github_transport,
        store=store,
    )
