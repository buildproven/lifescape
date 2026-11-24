"""BUI-2207: ChatGPT apps (OpenAI Apps SDK) can sign in to the hosted Lifescape server.

Rules from https://developers.openai.com/apps-sdk/build/auth (read 2026-11-23):

- REQ-CHATGPT-1: a client registering ``https://chatgpt.com/connector_platform_oauth_redirect``
  or ``https://chatgpt.com/connector/oauth/{callback_id}`` is accepted; every other
  non-Claude, non-loopback redirect is still refused.
- REQ-CHATGPT-2: the server metadata lists ``S256``, a ``registration_endpoint`` and the
  token endpoint auth method ``none``.
- REQ-CHATGPT-3: a ``resource`` on the authorization request binds the token to that audience,
  and a token for any audience other than the Lifescape MCP server gets 401 on ``/api/mcp``.

The flows reuse the in-process app and GitHub stub of ``tests/test_oauth.py``.
"""

from __future__ import annotations

from pathlib import Path

import httpx2
import jwt
import pytest
from test_oauth import (
    BASE,
    CLIENT_STATE,
    GITHUB_CALLBACK_PATH,
    OWNER,
    SIGNING_KEY,
    VERIFIER,
    _app,
    _authorize,
    _code_from,
    _exchange,
    _forged,
    _initialize,
    _query,
    _register,
    _run,
)

from lifescape.oauth import allowed_redirect

CHATGPT_STABLE = "https://chatgpt.com/connector_platform_oauth_redirect"
CHATGPT_CALLBACK = "https://chatgpt.com/connector/oauth/cb_6f1A-2.x~9"
CHATGPT_REDIRECTS = [CHATGPT_STABLE, CHATGPT_CALLBACK]


async def _register_raw(client: httpx2.AsyncClient, redirect: str) -> httpx2.Response:
    return await client.post(
        "/register",
        json={
            "redirect_uris": [redirect],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "client_name": "ChatGPT",
        },
    )


# ---------------------------------------------------------------- REQ-CHATGPT-1


@pytest.mark.parametrize("redirect", CHATGPT_REDIRECTS, ids=["stable", "callback-id"])
def test_req_chatgpt_1_both_chatgpt_redirect_forms_register(tmp_path: Path, redirect: str) -> None:
    async def body(client: httpx2.AsyncClient) -> httpx2.Response:
        return await _register_raw(client, redirect)

    response = _run(_app(tmp_path), body)
    assert response.status_code == 201, response.text
    assert response.json()["redirect_uris"] == [redirect]
    assert response.json()["token_endpoint_auth_method"] == "none"


@pytest.mark.parametrize(
    "redirect",
    [
        "https://chatgpt.com.evil.example/x",
        "https://chatgpt.com.evil.example/connector_platform_oauth_redirect",
        "https://evil.example/connector/oauth/abc",
        "https://chatgpt.com/other",
    ],
)
def test_req_chatgpt_1_lookalike_redirects_do_not_register(tmp_path: Path, redirect: str) -> None:
    async def body(client: httpx2.AsyncClient) -> httpx2.Response:
        return await _register_raw(client, redirect)

    response = _run(_app(tmp_path), body)
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_redirect_uri"
    assert CHATGPT_STABLE in response.json()["error_description"]


@pytest.mark.parametrize(
    ("uri", "allowed"),
    [
        (CHATGPT_STABLE, True),
        (CHATGPT_CALLBACK, True),
        ("https://chatgpt.com/connector/oauth/abc123", True),
        ("https://chatgpt.com.evil.example/x", False),
        ("https://evil.chatgpt.com/connector_platform_oauth_redirect", False),
        ("http://chatgpt.com/connector_platform_oauth_redirect", False),
        ("https://chatgpt.com:8443/connector_platform_oauth_redirect", False),
        ("https://user@chatgpt.com/connector_platform_oauth_redirect", False),
        (f"{CHATGPT_STABLE}/", False),
        (f"{CHATGPT_STABLE}?next=https://evil.example", False),
        (f"{CHATGPT_STABLE}#x", False),
        ("https://chatgpt.com/connector/oauth/", False),
        ("https://chatgpt.com/connector/oauth/a/b", False),
        ("https://chatgpt.com/connector/oauth/.", False),
        ("https://chatgpt.com/connector/oauth/..", False),
        ("https://chatgpt.com/connector/oauth/abc?x=1", False),
        ("https://chatgpt.com/connector/oauth/abc#x", False),
        ("https://chatgpt.com/connector/oauth/abc%2F..", False),
        ("https://chatgpt.com/connector/oauth/abc\n", False),
        ("https://chatgpt.com/connector/oauth/" + "a" * 129, False),
        ("https://chatgpt.com/connector/oauthabc", False),
    ],
)
def test_req_chatgpt_1_only_the_exact_chatgpt_redirects_are_allowed(
    uri: str, allowed: bool
) -> None:
    assert allowed_redirect(uri) is allowed


def test_req_chatgpt_1_authorize_refuses_another_callback_id(tmp_path: Path) -> None:
    async def body(client: httpx2.AsyncClient) -> httpx2.Response:
        registered = await _register(client, CHATGPT_CALLBACK)
        return await _authorize(
            client, registered["client_id"], "https://chatgpt.com/connector/oauth/other"
        )

    response = _run(_app(tmp_path), body)
    assert response.status_code == 400
    assert "location" not in response.headers


@pytest.mark.parametrize("redirect", CHATGPT_REDIRECTS, ids=["stable", "callback-id"])
def test_req_chatgpt_1_sign_in_returns_a_code_to_chatgpt(tmp_path: Path, redirect: str) -> None:
    async def body(client: httpx2.AsyncClient) -> httpx2.Response:
        registered = await _register(client, redirect)
        start = await _authorize(client, registered["client_id"], redirect, resource=BASE)
        assert start.status_code == 302, start.text
        github_state = _query(start.headers["location"])["state"]
        return await client.get(
            GITHUB_CALLBACK_PATH, params={"code": "gh-code", "state": github_state}
        )

    callback = _run(_app(tmp_path), body)
    assert _code_from(callback, redirect)
    assert callback.headers["location"].startswith(f"{redirect}?")
    assert _query(callback.headers["location"])["state"] == CLIENT_STATE


# ---------------------------------------------------------------- REQ-CHATGPT-2


def test_req_chatgpt_2_metadata_has_s256_registration_and_public_clients(
    tmp_path: Path,
) -> None:
    async def body(client: httpx2.AsyncClient) -> httpx2.Response:
        return await client.get("/.well-known/oauth-authorization-server")

    response = _run(_app(tmp_path), body)
    assert response.status_code == 200
    metadata = response.json()
    assert "S256" in metadata["code_challenge_methods_supported"]
    assert metadata["registration_endpoint"] == f"{BASE}/register"
    assert "none" in metadata["token_endpoint_auth_methods_supported"]


# ---------------------------------------------------------------- REQ-CHATGPT-3


def test_req_chatgpt_3_resource_binds_the_token_to_the_mcp_server(tmp_path: Path) -> None:
    async def body(client: httpx2.AsyncClient) -> tuple[httpx2.Response, httpx2.Response]:
        registered = await _register(client, CHATGPT_STABLE)
        start = await _authorize(client, registered["client_id"], CHATGPT_STABLE, resource=BASE)
        assert start.status_code == 302, start.text
        github_state = _query(start.headers["location"])["state"]
        callback = await client.get(
            GITHUB_CALLBACK_PATH, params={"code": "gh-code", "state": github_state}
        )
        issued = await _exchange(
            client, registered, _code_from(callback, CHATGPT_STABLE), VERIFIER, CHATGPT_STABLE
        )
        assert issued.status_code == 200, issued.text
        return issued, await _initialize(client, issued.json()["access_token"])

    issued, served = _run(_app(tmp_path), body)
    access = issued.json()["access_token"]
    claims = jwt.decode(access, SIGNING_KEY, algorithms=["HS256"], audience=BASE)
    assert claims["aud"] == BASE
    assert claims["sub"] == OWNER
    assert served.status_code == 200, served.text


def test_req_chatgpt_3_authorize_for_another_resource_is_refused(tmp_path: Path) -> None:
    async def body(client: httpx2.AsyncClient) -> httpx2.Response:
        registered = await _register(client, CHATGPT_STABLE)
        return await _authorize(
            client, registered["client_id"], CHATGPT_STABLE, resource="https://other-mcp.example"
        )

    response = _run(_app(tmp_path), body)
    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith(f"{CHATGPT_STABLE}?")
    assert _query(location)["error"] == "invalid_target"


@pytest.mark.parametrize(
    "audience",
    ["https://other-mcp.example", f"{BASE}/api/mcp/other", "https://chatgpt.com"],
)
def test_req_chatgpt_3_a_token_for_another_audience_gets_401_on_mcp(
    tmp_path: Path, audience: str
) -> None:
    async def body(client: httpx2.AsyncClient) -> httpx2.Response:
        return await _initialize(client, _forged({"aud": audience}))

    response = _run(_app(tmp_path), body)
    assert response.status_code == 401
    assert "resource_metadata=" in response.headers["www-authenticate"]
