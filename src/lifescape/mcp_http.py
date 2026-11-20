"""Hosted Lifescape MCP endpoint: Streamable HTTP behind an OAuth 2.0 resource server.

Agent-first surface (machine-setup ``docs/AGENT-FIRST.md``): chat on web and mobile reaches
Lifescape only over HTTP, so this app serves the same tools as the stdio server
(``lifescape.mcp_server.create_server``) at ``/mcp``. It never issues tokens; sign-in belongs to
the authorization server named by ``issuer_url``.

- A request to ``/mcp`` without a valid bearer token gets 401 with
  ``WWW-Authenticate: Bearer resource_metadata="<origin>/.well-known/oauth-protected-resource"``.
- ``/.well-known/oauth-protected-resource`` serves RFC 9728 metadata naming the issuer. The
  resource identifier is the public origin: RFC 9728 §3.1 builds the metadata URL from the
  resource, so a root metadata path names the origin, and the metadata stays self-consistent
  for clients that check §3.3.
- A token counts only when ``token_verifier`` accepts it and reports it was issued for that
  resource (``AccessToken.resource``, the RFC 8707 resource indicator).

``create_owner_http_app`` is the hosted app: the same origin also serves the authorization
server (``lifescape.oauth``) that signs the owner in through GitHub and issues those tokens.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from urllib.parse import SplitResult, urlsplit

import httpx2
from mcp.server.auth.provider import TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette

from lifescape.mcp_server import create_server
from lifescape.oauth import OAuthSettings, OwnerAuthorizationServer

MCP_PATH = "/mcp"
RESOURCE_METADATA_PATH = "/.well-known/oauth-protected-resource"
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
) -> Starlette:
    """Build the ASGI app for the hosted endpoint at ``<base_url>/mcp``.

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
) -> Starlette:
    """Build the hosted app: the MCP endpoint and its owner-only authorization server.

    Reads the OAuth settings from ``env`` first, so a missing variable stops the server at
    start (``OAuthConfigError`` names it). ``github_transport`` replaces the network for tests.
    """
    settings = OAuthSettings.from_env(env)
    origin = public_origin(base_url)
    authorization = OwnerAuthorizationServer(
        settings, issuer=origin, resource=origin, github_transport=github_transport
    )
    app = create_http_app(
        output_dir=output_dir,
        base_url=origin,
        issuer_url=origin,
        token_verifier=authorization,
        config_dir=config_dir,
    )
    app.router.routes.extend(authorization.routes())
    return app
