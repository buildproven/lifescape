"""BUI-1903: the hosted Lifescape MCP endpoint signs its owner in through GitHub OAuth.

Each test drives the hosted ASGI app in process (httpx2 ``ASGITransport``, lifespan started by
the test). GitHub is a stub (httpx2 ``MockTransport``) that answers the token exchange and the
``/user`` lookup, so no test reaches the network.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import anyio
import httpx2
import jwt
import pytest
from mcp.server.auth.provider import AccessToken
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl
from starlette.applications import Starlette

from lifescape.mcp_http import create_owner_http_app
from lifescape.oauth import (
    CLAUDE_REDIRECT_URI,
    GITHUB_CALLBACK_PATH,
    GITHUB_CLIENT_ID_ENV,
    GITHUB_CLIENT_SECRET_ENV,
    OWNER_ENV,
    REQUIRED_ENV,
    SIGNING_KEY_ENV,
    OAuthConfigError,
    OAuthSettings,
    OwnerAuthorizationServer,
    allowed_redirect,
)

BASE = "https://lifescape.example"
OWNER = "lifescape-owner"
SIGNING_KEY = "k" * 48
ENV = {
    OWNER_ENV: OWNER,
    GITHUB_CLIENT_ID_ENV: "gh-client-id",
    GITHUB_CLIENT_SECRET_ENV: "gh-client-secret",
    SIGNING_KEY_ENV: SIGNING_KEY,
}
CLAUDE_CALLBACK = "https://claude.ai/api/mcp/auth_callback"
VERIFIER = "v" * 64
CLIENT_STATE = "claude-state-123"
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


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


@dataclass
class GitHubStub:
    """Answers GitHub's OAuth token exchange and ``/user`` lookup with ``login``."""

    login: str = OWNER
    token_status: int = 200
    user_text: str | None = None
    seen: list[httpx2.Request] = field(default_factory=list)

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.seen.append(request)
        if request.url == "https://github.com/login/oauth/access_token":
            if self.token_status != 200:
                return httpx2.Response(self.token_status, json={"error": "server"})
            form = json.loads(request.content)
            if form.get("code") != "gh-code" or form.get("client_secret") != "gh-client-secret":
                return httpx2.Response(200, json={"error": "bad_verification_code"})
            return httpx2.Response(200, json={"access_token": "gh-token", "token_type": "bearer"})
        if request.url == "https://api.github.com/user":
            if request.headers.get("authorization") != "Bearer gh-token":
                return httpx2.Response(401, json={"message": "Bad credentials"})
            if self.user_text is not None:
                return httpx2.Response(200, text=self.user_text)
            return httpx2.Response(200, json={"login": self.login, "id": 1})
        return httpx2.Response(404)

    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self.handle)


def _app(
    tmp_path: Path, github: GitHubStub | None = None, env: Mapping[str, str] = ENV
) -> Starlette:
    return create_owner_http_app(
        output_dir=tmp_path,
        base_url=BASE,
        env=env,
        github_transport=(github or GitHubStub()).transport(),
    )


@asynccontextmanager
async def _http(app: Starlette) -> AsyncIterator[httpx2.AsyncClient]:
    async with (
        app.router.lifespan_context(app),
        httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url=BASE) as client,
    ):
        yield client


def _run[T](app: Starlette, body: Callable[[httpx2.AsyncClient], Awaitable[T]]) -> T:
    async def main() -> T:
        async with _http(app) as client:
            return await body(client)

    return anyio.run(main)


def _query(url: str) -> dict[str, str]:
    return {name: values[0] for name, values in parse_qs(urlsplit(url).query).items()}


async def _register(
    client: httpx2.AsyncClient, redirect: str = CLAUDE_CALLBACK, method: str = "none"
) -> dict[str, Any]:
    response = await client.post(
        "/register",
        json={
            "redirect_uris": [redirect],
            "token_endpoint_auth_method": method,
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "client_name": "Claude",
        },
    )
    assert response.status_code == 201, response.text
    registered: dict[str, Any] = response.json()
    return registered


async def _authorize(
    client: httpx2.AsyncClient, client_id: str, redirect: str = CLAUDE_CALLBACK, **extra: str
) -> httpx2.Response:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect,
        "code_challenge": _challenge(VERIFIER),
        "code_challenge_method": "S256",
        "state": CLIENT_STATE,
        **extra,
    }
    return await client.get("/authorize", params=params)


async def _sign_in(
    client: httpx2.AsyncClient, redirect: str = CLAUDE_CALLBACK, method: str = "none"
) -> tuple[dict[str, Any], httpx2.Response]:
    """Register, authorize, and come back from GitHub; returns the client and the callback."""
    registered = await _register(client, redirect, method)
    start = await _authorize(client, registered["client_id"], redirect)
    assert start.status_code == 302, start.text
    github_state = _query(start.headers["location"])["state"]
    callback = await client.get(
        GITHUB_CALLBACK_PATH, params={"code": "gh-code", "state": github_state}
    )
    return registered, callback


async def _exchange(
    client: httpx2.AsyncClient,
    registered: Mapping[str, Any],
    code: str,
    verifier: str = VERIFIER,
    redirect: str = CLAUDE_CALLBACK,
) -> httpx2.Response:
    form = {
        "grant_type": "authorization_code",
        "client_id": registered["client_id"],
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": redirect,
    }
    if registered.get("client_secret"):
        form["client_secret"] = registered["client_secret"]
    return await client.post("/token", data=form)


def _initialize(client: httpx2.AsyncClient, token: str) -> Awaitable[httpx2.Response]:
    return client.post(
        "/api/mcp", json=INITIALIZE, headers={**JSON_HEADERS, "Authorization": f"Bearer {token}"}
    )


# ------------------------------------------------------------------ REQ-OAUTH-1


def test_req_oauth_1_metadata_names_authorize_token_registration_and_s256(
    tmp_path: Path,
) -> None:
    async def body(client: httpx2.AsyncClient) -> httpx2.Response:
        return await client.get("/.well-known/oauth-authorization-server")

    response = _run(_app(tmp_path), body)
    assert response.status_code == 200
    metadata = response.json()
    assert metadata["issuer"] == BASE
    assert metadata["authorization_endpoint"] == f"{BASE}/authorize"
    assert metadata["token_endpoint"] == f"{BASE}/token"
    assert metadata["registration_endpoint"] == f"{BASE}/register"
    assert metadata["code_challenge_methods_supported"] == ["S256"]
    assert metadata["response_types_supported"] == ["code"]
    assert set(metadata["grant_types_supported"]) == {"authorization_code", "refresh_token"}
    assert "none" in metadata["token_endpoint_auth_methods_supported"]


def test_req_oauth_1_protected_resource_names_this_server_as_issuer(tmp_path: Path) -> None:
    async def body(client: httpx2.AsyncClient) -> httpx2.Response:
        return await client.get("/.well-known/oauth-protected-resource")

    metadata = _run(_app(tmp_path), body).json()
    assert metadata["resource"] == BASE
    assert metadata["authorization_servers"] == [BASE]


def test_req_oauth_1_registration_refuses_a_foreign_redirect(tmp_path: Path) -> None:
    async def body(client: httpx2.AsyncClient) -> httpx2.Response:
        return await client.post(
            "/register",
            json={"redirect_uris": ["https://attacker.example/callback"], "client_name": "x"},
        )

    response = _run(_app(tmp_path), body)
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_redirect_uri"


@pytest.mark.parametrize(
    "payload",
    [
        b"not json",
        b'{"redirect_uris": []}',
        b'{"redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],'
        b' "token_endpoint_auth_method": "private_key_jwt"}',
        b'{"redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],'
        b' "grant_types": ["client_credentials"]}',
        b'{"redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],'
        b' "response_types": ["token"]}',
        b'{"redirect_uris": ["https://claude.ai/api/mcp/auth_callback"], "scope": "admin"}',
    ],
    ids=["not-json", "no-redirects", "private-key-jwt", "no-code-grant", "no-code", "scope"],
)
def test_req_oauth_1_registration_refuses_invalid_metadata(tmp_path: Path, payload: bytes) -> None:
    async def body(client: httpx2.AsyncClient) -> httpx2.Response:
        return await client.post(
            "/register", content=payload, headers={"content-type": "application/json"}
        )

    response = _run(_app(tmp_path), body)
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_client_metadata"


def test_req_oauth_1_authorize_without_pkce_is_refused(tmp_path: Path) -> None:
    async def body(client: httpx2.AsyncClient) -> httpx2.Response:
        registered = await _register(client)
        return await client.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": registered["client_id"],
                "redirect_uri": CLAUDE_CALLBACK,
                "state": CLIENT_STATE,
            },
        )

    response = _run(_app(tmp_path), body)
    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith(CLAUDE_CALLBACK)
    assert _query(location)["error"] == "invalid_request"
    assert "code" not in _query(location)


def test_req_oauth_1_authorize_with_plain_pkce_is_refused(tmp_path: Path) -> None:
    async def body(client: httpx2.AsyncClient) -> httpx2.Response:
        registered = await _register(client)
        return await _authorize(client, registered["client_id"], code_challenge_method="plain")

    location = _run(_app(tmp_path), body).headers["location"]
    assert _query(location)["error"] == "invalid_request"


def test_req_oauth_1_authorize_with_an_unknown_client_does_not_redirect(tmp_path: Path) -> None:
    async def body(client: httpx2.AsyncClient) -> httpx2.Response:
        return await _authorize(client, "made-up-client")

    response = _run(_app(tmp_path), body)
    assert response.status_code == 400
    assert "location" not in response.headers


def test_req_oauth_1_authorize_for_another_resource_is_refused(tmp_path: Path) -> None:
    async def body(client: httpx2.AsyncClient) -> httpx2.Response:
        registered = await _register(client)
        return await _authorize(
            client, registered["client_id"], resource="https://elsewhere.example"
        )

    location = _run(_app(tmp_path), body).headers["location"]
    assert _query(location)["error"] == "invalid_target"


def test_req_oauth_1_authorize_sends_the_owner_to_github(tmp_path: Path) -> None:
    async def body(client: httpx2.AsyncClient) -> httpx2.Response:
        registered = await _register(client)
        return await _authorize(client, registered["client_id"], resource=f"{BASE}/")

    response = _run(_app(tmp_path), body)
    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith("https://github.com/login/oauth/authorize?")
    query = _query(location)
    assert query["client_id"] == "gh-client-id"
    assert query["redirect_uri"] == f"{BASE}{GITHUB_CALLBACK_PATH}"
    assert query["state"]
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert f"Path={GITHUB_CALLBACK_PATH}" in cookie


def test_req_oauth_1_authorize_with_a_malformed_challenge_is_refused(tmp_path: Path) -> None:
    async def body(client: httpx2.AsyncClient) -> httpx2.Response:
        registered = await _register(client)
        return await _authorize(client, registered["client_id"], code_challenge="short")

    location = _run(_app(tmp_path), body).headers["location"]
    assert _query(location)["error"] == "invalid_request"


def test_req_oauth_1_authorize_with_an_overlong_state_is_refused(tmp_path: Path) -> None:
    async def body(client: httpx2.AsyncClient) -> httpx2.Response:
        registered = await _register(client)
        return await _authorize(client, registered["client_id"], state="s" * 513)

    location = _run(_app(tmp_path), body).headers["location"]
    assert _query(location)["error"] == "invalid_request"


def test_req_oauth_1_authorize_without_redirect_uses_the_one_registered(tmp_path: Path) -> None:
    async def body(client: httpx2.AsyncClient) -> httpx2.Response:
        registered = await _register(client)
        return await client.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": registered["client_id"],
                "code_challenge": _challenge(VERIFIER),
                "code_challenge_method": "S256",
            },
        )

    response = _run(_app(tmp_path), body)
    assert response.headers["location"].startswith("https://github.com/login/oauth/authorize?")


def test_req_oauth_1_loopback_redirect_matches_on_any_port(tmp_path: Path) -> None:
    async def body(client: httpx2.AsyncClient) -> list[httpx2.Response]:
        registered = await _register(client, "http://localhost/callback")
        return [
            await _authorize(client, registered["client_id"], "http://localhost:53682/callback"),
            await _authorize(client, registered["client_id"], "http://127.0.0.1:53682/callback"),
        ]

    same_host, other_host = _run(_app(tmp_path), body)
    assert same_host.headers["location"].startswith("https://github.com/login/oauth/authorize?")
    assert other_host.status_code == 400
    assert "location" not in other_host.headers


def test_req_oauth_1_registration_defaults_to_a_secret_client(tmp_path: Path) -> None:
    async def body(client: httpx2.AsyncClient) -> httpx2.Response:
        return await client.post(
            "/register", json={"redirect_uris": [CLAUDE_CALLBACK], "scope": "mcp"}
        )

    registered = _run(_app(tmp_path), body).json()
    assert registered["token_endpoint_auth_method"] == "client_secret_post"
    assert registered["client_secret"]
    assert registered["client_secret_expires_at"] == 0
    assert registered["scope"] == "mcp"


@pytest.mark.parametrize(
    ("uri", "allowed"),
    [
        (CLAUDE_REDIRECT_URI, True),
        ("http://localhost/callback", True),
        ("http://127.0.0.1:8080/callback", True),
        ("https://localhost/callback", False),
        ("http://localhost:99999/callback", False),
        ("http://localhost/callback?x=1", False),
        ("http://localhost/other", False),
        ("http://user@localhost/callback", False),
        ("http://evil.example/callback", False),
        (f"{CLAUDE_REDIRECT_URI}/", False),
    ],
)
def test_req_oauth_1_only_claude_and_loopback_redirects_are_allowed(
    uri: str, allowed: bool
) -> None:
    assert allowed_redirect(uri) is allowed


# ------------------------------------------------------------------ REQ-OAUTH-2


def test_req_oauth_2_another_github_user_gets_403_and_no_code(tmp_path: Path) -> None:
    github = GitHubStub(login="someone-else")

    async def body(client: httpx2.AsyncClient) -> httpx2.Response:
        _, callback = await _sign_in(client)
        return callback

    response = _run(_app(tmp_path, github), body)
    assert response.status_code == 403
    assert "location" not in response.headers
    assert "code=" not in response.text
    assert any(request.url == "https://api.github.com/user" for request in github.seen)


def test_req_oauth_2_owner_login_matches_case_insensitively(tmp_path: Path) -> None:
    async def body(client: httpx2.AsyncClient) -> httpx2.Response:
        _, callback = await _sign_in(client)
        return callback

    response = _run(_app(tmp_path, GitHubStub(login=OWNER.upper())), body)
    assert response.status_code == 302


def test_req_oauth_2_callback_from_another_browser_is_refused(tmp_path: Path) -> None:
    github = GitHubStub()

    async def body(client: httpx2.AsyncClient) -> httpx2.Response:
        registered = await _register(client)
        start = await _authorize(client, registered["client_id"])
        state = _query(start.headers["location"])["state"]
        client.cookies.clear()
        return await client.get(GITHUB_CALLBACK_PATH, params={"code": "gh-code", "state": state})

    response = _run(_app(tmp_path, github), body)
    assert response.status_code == 400
    assert "location" not in response.headers
    assert github.seen == []


@pytest.mark.parametrize(
    "params",
    [
        {"code": "gh-code"},
        {"code": "gh-code", "state": "forged"},
        {"state": "x", "error": "denied"},
    ],
    ids=["no-state", "forged-state", "github-error"],
)
def test_req_oauth_2_incomplete_or_forged_callback_is_refused(
    tmp_path: Path, params: dict[str, str]
) -> None:
    async def body(client: httpx2.AsyncClient) -> httpx2.Response:
        return await client.get(GITHUB_CALLBACK_PATH, params=params)

    response = _run(_app(tmp_path), body)
    assert response.status_code == 400
    assert "location" not in response.headers


def test_req_oauth_2_github_denial_after_a_valid_start_is_refused(tmp_path: Path) -> None:
    async def body(client: httpx2.AsyncClient) -> httpx2.Response:
        registered = await _register(client)
        start = await _authorize(client, registered["client_id"])
        state = _query(start.headers["location"])["state"]
        return await client.get(
            GITHUB_CALLBACK_PATH, params={"error": "access_denied", "state": state}
        )

    response = _run(_app(tmp_path), body)
    assert response.status_code == 400
    assert "location" not in response.headers


@pytest.mark.parametrize(
    "github",
    [
        GitHubStub(token_status=500),
        GitHubStub(login=""),
        GitHubStub(user_text="<html>"),
        GitHubStub(user_text="[]"),
    ],
    ids=["token-500", "no-login", "not-json", "not-object"],
)
def test_req_oauth_2_github_failure_is_502_and_no_code(tmp_path: Path, github: GitHubStub) -> None:
    async def body(client: httpx2.AsyncClient) -> httpx2.Response:
        _, callback = await _sign_in(client)
        return callback

    response = _run(_app(tmp_path, github), body)
    assert response.status_code == 502
    assert "location" not in response.headers


def test_req_oauth_2_unreachable_github_is_502(tmp_path: Path) -> None:
    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("down", request=request)

    app = create_owner_http_app(
        output_dir=tmp_path, base_url=BASE, env=ENV, github_transport=httpx2.MockTransport(refuse)
    )

    async def body(client: httpx2.AsyncClient) -> httpx2.Response:
        _, callback = await _sign_in(client)
        return callback

    assert _run(app, body).status_code == 502


# ------------------------------------------------------------------ REQ-OAUTH-3


def _code_from(callback: httpx2.Response, redirect: str = CLAUDE_CALLBACK) -> str:
    assert callback.status_code == 302, callback.text
    location = callback.headers["location"]
    assert location.startswith(redirect)
    query = _query(location)
    assert query["state"] == CLIENT_STATE
    return query["code"]


@pytest.mark.parametrize(
    ("redirect", "method"),
    [
        (CLAUDE_CALLBACK, "none"),
        (CLAUDE_CALLBACK, "client_secret_post"),
        ("http://localhost:33418/callback", "none"),
    ],
    ids=["claude-public", "claude-secret", "loopback"],
)
def test_req_oauth_3_owner_sign_in_issues_a_token_the_mcp_endpoint_accepts(
    tmp_path: Path, redirect: str, method: str
) -> None:
    async def body(client: httpx2.AsyncClient) -> tuple[httpx2.Response, httpx2.Response]:
        registered, callback = await _sign_in(client, redirect, method)
        issued = await _exchange(
            client, registered, _code_from(callback, redirect), VERIFIER, redirect
        )
        assert issued.status_code == 200, issued.text
        return issued, await _initialize(client, issued.json()["access_token"])

    issued, served = _run(_app(tmp_path), body)
    tokens = issued.json()
    assert tokens["token_type"] == "Bearer"
    assert tokens["expires_in"] > 0
    assert tokens["refresh_token"]
    assert served.status_code == 200, served.text
    assert served.json()["result"]["serverInfo"]


def test_req_oauth_3_access_token_names_owner_issuer_and_resource(tmp_path: Path) -> None:
    async def body(client: httpx2.AsyncClient) -> httpx2.Response:
        registered, callback = await _sign_in(client)
        return await _exchange(client, registered, _code_from(callback))

    access = _run(_app(tmp_path), body).json()["access_token"]
    claims = jwt.decode(access, SIGNING_KEY, algorithms=["HS256"], audience=BASE)
    assert claims["sub"] == OWNER
    assert claims["iss"] == BASE
    assert jwt.get_unverified_header(access)["typ"] == "at+jwt"


def test_req_oauth_3_wrong_verifier_gets_no_token_and_spends_the_code(tmp_path: Path) -> None:
    async def body(client: httpx2.AsyncClient) -> tuple[httpx2.Response, httpx2.Response]:
        registered, callback = await _sign_in(client)
        code = _code_from(callback)
        wrong = await _exchange(client, registered, code, verifier="w" * 64)
        return wrong, await _exchange(client, registered, code)

    wrong, retry = _run(_app(tmp_path), body)
    assert wrong.status_code == 400
    assert wrong.json()["error"] == "invalid_grant"
    assert "access_token" not in wrong.json()
    assert retry.status_code == 400
    assert retry.json()["error"] == "invalid_grant"


def test_req_oauth_3_a_code_is_spent_on_first_use(tmp_path: Path) -> None:
    async def body(client: httpx2.AsyncClient) -> tuple[httpx2.Response, httpx2.Response]:
        registered, callback = await _sign_in(client)
        code = _code_from(callback)
        return await _exchange(client, registered, code), await _exchange(client, registered, code)

    first, replay = _run(_app(tmp_path), body)
    assert first.status_code == 200
    assert replay.status_code == 400
    assert replay.json()["error"] == "invalid_grant"


def test_req_oauth_3_a_code_for_another_client_or_redirect_is_refused(tmp_path: Path) -> None:
    async def body(client: httpx2.AsyncClient) -> list[httpx2.Response]:
        registered, callback = await _sign_in(client)
        code = _code_from(callback)
        other = await _register(client)
        return [
            await _exchange(client, other, code),
            await _exchange(client, registered, code, redirect="http://localhost/callback"),
        ]

    for response in _run(_app(tmp_path), body):
        assert response.status_code == 400
        assert "access_token" not in response.json()


def test_req_oauth_3_secret_client_without_its_secret_is_refused(tmp_path: Path) -> None:
    async def body(client: httpx2.AsyncClient) -> httpx2.Response:
        registered, callback = await _sign_in(client, method="client_secret_post")
        return await _exchange(
            client, {**registered, "client_secret": "guess"}, _code_from(callback)
        )

    response = _run(_app(tmp_path), body)
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_client"


def test_req_oauth_3_refresh_rotates_and_the_old_refresh_token_is_spent(tmp_path: Path) -> None:
    async def body(client: httpx2.AsyncClient) -> tuple[Any, ...]:
        registered, callback = await _sign_in(client)
        tokens = (await _exchange(client, registered, _code_from(callback))).json()
        form = {
            "grant_type": "refresh_token",
            "client_id": registered["client_id"],
            "refresh_token": tokens["refresh_token"],
        }
        refreshed = await client.post("/token", data=form)
        replay = await client.post("/token", data=form)
        served = await _initialize(client, refreshed.json()["access_token"])
        return refreshed, replay, served

    refreshed, replay, served = _run(_app(tmp_path), body)
    assert refreshed.status_code == 200, refreshed.text
    assert served.status_code == 200
    assert replay.status_code == 400
    assert replay.json()["error"] == "invalid_grant"


def test_req_oauth_3_a_refresh_token_for_another_client_is_refused(tmp_path: Path) -> None:
    async def body(client: httpx2.AsyncClient) -> httpx2.Response:
        registered, callback = await _sign_in(client)
        tokens = (await _exchange(client, registered, _code_from(callback))).json()
        other = await _register(client)
        form = {
            "grant_type": "refresh_token",
            "client_id": other["client_id"],
            "refresh_token": tokens["refresh_token"],
        }
        return await client.post("/token", data=form)

    response = _run(_app(tmp_path), body)
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant"


def test_req_oauth_3_a_new_owner_voids_grants_issued_to_the_old_one(tmp_path: Path) -> None:
    """Changing OWNER_GITHUB_LOGIN stops the old owner's codes and refresh tokens."""

    async def body(client: httpx2.AsyncClient) -> dict[str, Any]:
        registered, callback = await _sign_in(client)
        code = _code_from(callback)
        refresher, second = await _sign_in(client)
        issued = (await _exchange(client, refresher, _code_from(second))).json()
        return {
            "registered": registered,
            "code": code,
            "refresher": refresher,
            "refresh": issued["refresh_token"],
        }

    grants = _run(_app(tmp_path), body)
    renamed = create_owner_http_app(
        output_dir=tmp_path, base_url=BASE, env={**ENV, OWNER_ENV: "new-owner"}
    )

    async def replay(client: httpx2.AsyncClient) -> list[httpx2.Response]:
        refresh = {
            "grant_type": "refresh_token",
            "client_id": grants["refresher"]["client_id"],
            "refresh_token": grants["refresh"],
        }
        return [
            await _exchange(client, grants["registered"], grants["code"]),
            await client.post("/token", data=refresh),
        ]

    for response in _run(renamed, replay):
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_grant"


def test_req_oauth_3_registration_and_revocation_hooks_are_not_mounted() -> None:
    provider = OwnerAuthorizationServer(OAuthSettings.from_env(ENV), issuer=BASE, resource=BASE)

    async def calls() -> None:
        with pytest.raises(NotImplementedError):
            await provider.register_client(
                OAuthClientInformationFull(client_id="x", redirect_uris=[AnyUrl(CLAUDE_CALLBACK)])
            )
        with pytest.raises(NotImplementedError):
            await provider.revoke_token(AccessToken(token="t", client_id="x", scopes=[]))

    anyio.run(calls)


def _forged(claims: dict[str, Any], key: str = SIGNING_KEY, typ: str = "at+jwt") -> str:
    now = int(time.time())
    base = {"iss": BASE, "aud": BASE, "sub": OWNER, "client_id": "c", "scope": "mcp"}
    return jwt.encode(
        {**base, "iat": now, "exp": now + 600, "jti": "j", **claims},
        key,
        algorithm="HS256",
        headers={"typ": typ},
    )


@pytest.mark.parametrize(
    "token",
    [
        _forged({}, key="x" * 48),
        _forged({"exp": int(time.time()) - 60}),
        _forged({"aud": "https://elsewhere.example"}),
        _forged({"iss": "https://elsewhere.example"}),
        _forged({"sub": "someone-else"}),
        _forged({}, typ="lifescape-refresh+jwt"),
        _forged({"client_id": None}),
        "not-a-jwt",
    ],
    ids=[
        "other-key",
        "expired",
        "other-audience",
        "other-issuer",
        "not-owner",
        "refresh",
        "malformed-claims",
        "junk",
    ],
)
def test_req_oauth_3_tokens_this_server_did_not_issue_are_challenged(
    tmp_path: Path, token: str
) -> None:
    async def body(client: httpx2.AsyncClient) -> httpx2.Response:
        return await _initialize(client, token)

    response = _run(_app(tmp_path), body)
    assert response.status_code == 401
    assert "resource_metadata=" in response.headers["www-authenticate"]


def test_req_oauth_3_a_wellformed_owner_token_is_accepted(tmp_path: Path) -> None:
    async def body(client: httpx2.AsyncClient) -> httpx2.Response:
        return await _initialize(client, _forged({}))

    assert _run(_app(tmp_path), body).status_code == 200


# ------------------------------------------------------------------ REQ-OAUTH-4


@pytest.mark.parametrize("name", REQUIRED_ENV)
@pytest.mark.parametrize("value", [None, "", "   "], ids=["unset", "empty", "blank"])
def test_req_oauth_4_missing_env_var_fails_at_start_naming_it(
    tmp_path: Path, name: str, value: str | None
) -> None:
    env = {key: val for key, val in ENV.items() if key != name}
    if value is not None:
        env[name] = value
    with pytest.raises(OAuthConfigError) as raised:
        create_owner_http_app(output_dir=tmp_path, base_url=BASE, env=env)
    assert name in str(raised.value)
    for other in REQUIRED_ENV:
        if other != name:
            assert other not in str(raised.value)


def test_req_oauth_4_every_missing_env_var_is_named(tmp_path: Path) -> None:
    with pytest.raises(OAuthConfigError) as raised:
        OAuthSettings.from_env({})
    for name in REQUIRED_ENV:
        assert name in str(raised.value)


def test_req_oauth_4_required_env_names_the_four_settings() -> None:
    assert set(REQUIRED_ENV) == {
        "OWNER_GITHUB_LOGIN",
        "LIFESCAPE_GITHUB_OAUTH_CLIENT_ID",
        "LIFESCAPE_GITHUB_OAUTH_CLIENT_SECRET",
        "LIFESCAPE_OAUTH_SIGNING_KEY",
    }


def test_req_oauth_4_short_signing_key_fails_at_start(tmp_path: Path) -> None:
    with pytest.raises(OAuthConfigError) as raised:
        create_owner_http_app(
            output_dir=tmp_path, base_url=BASE, env={**ENV, SIGNING_KEY_ENV: "short"}
        )
    assert SIGNING_KEY_ENV in str(raised.value)


@pytest.mark.parametrize("login", ["not a login", "-dash-first", "x" * 40])
def test_req_oauth_4_invalid_owner_login_fails_at_start(tmp_path: Path, login: str) -> None:
    with pytest.raises(OAuthConfigError) as raised:
        OAuthSettings.from_env({**ENV, OWNER_ENV: login})
    assert OWNER_ENV in str(raised.value)


def test_req_oauth_4_settings_trim_surrounding_space() -> None:
    settings = OAuthSettings.from_env({**ENV, OWNER_ENV: f"  {OWNER}\n"})
    assert settings.owner_login == OWNER
