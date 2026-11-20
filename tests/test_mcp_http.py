"""BUI-1901: the hosted MCP endpoint is Streamable HTTP behind an OAuth resource server.

Each test drives the ASGI app in process (httpx2 ``ASGITransport``, lifespan started by the
test) with a static test token verifier. The stdio server is the reference for the tool set.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import anyio
import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server.auth.provider import AccessToken
from mcp_types import CallToolResult
from starlette.applications import Starlette

from lifescape.mcp_http import HttpConfigError, create_http_app
from lifescape.mcp_server import create_server

BASE = "https://lifescape.example"
ISSUER = "https://auth.lifescape.example"
METADATA_URL = f"{BASE}/.well-known/oauth-protected-resource"
CHALLENGE = f'resource_metadata="{METADATA_URL}"'
GOOD_BEARER = "good-bearer"
OTHER_RESOURCE_BEARER = "other-resource-bearer"
EXPIRED_BEARER = "expired-bearer"
UNKNOWN_BEARER = "not-a-known-bearer"
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


class StaticTokenVerifier:
    """Test verifier: knows three tokens and nothing else."""

    async def verify_token(self, token: str) -> AccessToken | None:
        tokens = {
            GOOD_BEARER: AccessToken(
                token=token, client_id="claude", scopes=[], resource=BASE, subject="owner"
            ),
            OTHER_RESOURCE_BEARER: AccessToken(
                token=token,
                client_id="claude",
                scopes=[],
                resource="https://elsewhere.example",
                subject="owner",
            ),
            EXPIRED_BEARER: AccessToken(
                token=token,
                client_id="claude",
                scopes=[],
                resource=BASE,
                expires_at=int(time.time()) - 60,
            ),
        }
        return tokens.get(token)


def _app(tmp_path: Path, *, base_url: str = BASE, issuer_url: str = ISSUER) -> Starlette:
    return create_http_app(
        output_dir=tmp_path,
        base_url=base_url,
        issuer_url=issuer_url,
        token_verifier=StaticTokenVerifier(),
    )


@asynccontextmanager
async def _http(
    app: Starlette, *, token: str | None = None, base_url: str = BASE
) -> AsyncIterator[httpx2.AsyncClient]:
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    async with (
        app.router.lifespan_context(app),
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url=base_url, headers=headers
        ) as client,
    ):
        yield client


def _run[T](app: Starlette, body: Callable[[httpx2.AsyncClient], Awaitable[T]], **kw: Any) -> T:
    async def main() -> T:
        async with _http(app, **kw) as client:
            return await body(client)

    return anyio.run(main)


def _post_initialize(client: httpx2.AsyncClient) -> Awaitable[httpx2.Response]:
    return client.post("/api/mcp", json=INITIALIZE, headers=JSON_HEADERS)


def _assert_challenge(response: httpx2.Response) -> None:
    assert response.status_code == 401
    challenge = response.headers["www-authenticate"]
    assert challenge.startswith("Bearer ")
    assert CHALLENGE in challenge


def test_req_http_1_post_without_token_gets_401_with_resource_metadata(tmp_path: Path) -> None:
    response = _run(_app(tmp_path), _post_initialize)
    _assert_challenge(response)
    assert response.json()["error"] == "invalid_token"


@pytest.mark.parametrize(
    "authorization",
    [
        f"Basic {GOOD_BEARER}",
        f"Bearer {UNKNOWN_BEARER}",
        f"Bearer {OTHER_RESOURCE_BEARER}",
        f"Bearer {EXPIRED_BEARER}",
        "Bearer ",
    ],
    ids=["basic-scheme", "unknown-token", "other-resource", "expired", "empty-bearer"],
)
def test_req_http_1_invalid_credentials_get_the_same_challenge(
    tmp_path: Path, authorization: str
) -> None:
    async def body(client: httpx2.AsyncClient) -> httpx2.Response:
        return await client.post(
            "/api/mcp", json=INITIALIZE, headers={**JSON_HEADERS, "Authorization": authorization}
        )

    _assert_challenge(_run(_app(tmp_path), body))


def test_req_http_1_get_and_delete_without_token_are_challenged(tmp_path: Path) -> None:
    async def body(client: httpx2.AsyncClient) -> list[httpx2.Response]:
        return [await client.get("/api/mcp"), await client.delete("/api/mcp")]

    for response in _run(_app(tmp_path), body):
        _assert_challenge(response)


def test_req_http_1_challenge_names_the_normalized_public_origin(tmp_path: Path) -> None:
    app = _app(tmp_path, base_url="https://LifeScape.example:443/")
    response = _run(app, _post_initialize)
    _assert_challenge(response)


def test_req_http_2_metadata_names_the_authorization_server(tmp_path: Path) -> None:
    async def body(client: httpx2.AsyncClient) -> httpx2.Response:
        return await client.get("/.well-known/oauth-protected-resource")

    response = _run(_app(tmp_path, issuer_url=f"{ISSUER}/"), body)
    assert response.status_code == 200
    metadata = response.json()
    assert metadata["resource"] == BASE
    assert metadata["authorization_servers"] == [ISSUER]
    assert metadata["bearer_methods_supported"] == ["header"]


def test_req_http_2_metadata_needs_no_token(tmp_path: Path) -> None:
    async def body(client: httpx2.AsyncClient) -> httpx2.Response:
        return await client.get("/.well-known/oauth-protected-resource")

    assert _run(_app(tmp_path), body, token=UNKNOWN_BEARER).status_code == 200


@pytest.mark.parametrize(
    ("base_url", "issuer_url"),
    [
        ("http://lifescape.example", ISSUER),
        (BASE, "http://auth.lifescape.example"),
        (f"{BASE}/api/mcp", ISSUER),
        (f"{BASE}?x=1", ISSUER),
        (f"{BASE}#top", ISSUER),
        ("https://user:pw@lifescape.example", ISSUER),
        ("https://lifescape.example:notaport", ISSUER),
        ("lifescape.example", ISSUER),
        (BASE, ""),
    ],
)
def test_req_http_2_insecure_or_ambiguous_urls_are_refused(
    tmp_path: Path, base_url: str, issuer_url: str
) -> None:
    with pytest.raises(HttpConfigError):
        _app(tmp_path, base_url=base_url, issuer_url=issuer_url)


@pytest.mark.parametrize(
    "local", ["http://127.0.0.1:8766", "http://[::1]:8766", "http://localhost"]
)
def test_req_http_2_loopback_http_is_allowed_for_local_development(
    tmp_path: Path, local: str
) -> None:
    async def body(client: httpx2.AsyncClient) -> httpx2.Response:
        return await client.get("/.well-known/oauth-protected-resource")

    app = _app(tmp_path, base_url=f"{local}/", issuer_url="http://localhost:9000/")
    metadata = _run(app, body, base_url=local).json()
    assert metadata["resource"] == local
    assert metadata["authorization_servers"] == ["http://localhost:9000"]


def _payload(result: CallToolResult) -> Any:
    assert not result.is_error, result
    return result.structured_content


def test_req_http_3_valid_token_serves_the_stdio_tools(tmp_path: Path) -> None:
    async def over_http() -> tuple[set[str], Any]:
        app = _app(tmp_path / "http")
        async with _http(app, token=GOOD_BEARER) as http_client:
            transport = streamable_http_client(f"{BASE}/api/mcp", http_client=http_client)
            async with Client(transport) as client:
                tools = {tool.name for tool in (await client.list_tools()).tools}
                found = await client.call_tool("find_place", {"query": "New Bern, NC"})
                return tools, _payload(found)

    async def over_stdio_server() -> tuple[set[str], Any]:
        server = create_server(output_dir=tmp_path / "stdio")
        async with Client(server, mode="legacy") as client:
            tools = {tool.name for tool in (await client.list_tools()).tools}
            found = await client.call_tool("find_place", {"query": "New Bern, NC"})
            return tools, _payload(found)

    http_tools, http_found = anyio.run(over_http)
    stdio_tools, stdio_found = anyio.run(over_stdio_server)
    assert http_tools == stdio_tools
    assert "find_place" in http_tools
    assert http_found == stdio_found
    assert http_found["matches"]


def test_req_http_3_valid_token_from_another_host_is_refused(tmp_path: Path) -> None:
    """DNS rebinding protection: only the configured public host is served."""
    response = _run(
        _app(tmp_path), _post_initialize, token=GOOD_BEARER, base_url="https://evil.example"
    )
    assert response.status_code == 421


def test_req_http_4_stdio_server_needs_no_token(tmp_path: Path) -> None:
    server = create_server(output_dir=tmp_path, auth=None, token_verifier=None)
    assert server.settings.auth is None

    async def tools() -> set[str]:
        async with Client(server, mode="legacy") as client:
            return {tool.name for tool in (await client.list_tools()).tools}

    assert "find_place" in anyio.run(tools)
