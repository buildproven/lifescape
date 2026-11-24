"""BUI-2207: ChatGPT (OpenAI Apps SDK) can sign in to the hosted Lifescape server.

Source for ChatGPT's OAuth rules: https://developers.openai.com/apps-sdk/build/auth (read
2026-11-23). Each test drives the hosted ASGI app in process with the GitHub stub of
``test_oauth``, so no test reaches the network.

- REQ-CHATGPT-1: ChatGPT's two redirect forms register; every other foreign redirect does not.
- REQ-CHATGPT-2: the server metadata lists S256, a registration endpoint, and auth method none.
- REQ-CHATGPT-3: a ``resource`` request binds the token to it; another audience gets 401.
"""

from __future__ import annotations

from pathlib import Path

import httpx2
import jwt
import pytest
from test_oauth import (
    BASE,
    SIGNING_KEY,
    _app,
    _authorize,
    _code_from,
    _exchange,
    _forged,
    _initialize,
    _query,
    _register,
    _run,
    _sign_in,
)

from lifescape.oauth import GITHUB_CALLBACK_PATH, allowed_redirect

CHATGPT_STABLE = "https://chatgpt.com/connector_platform_oauth_redirect"
CHATGPT_CALLBACK = "https://chatgpt.com/connector/oauth/cb_0a1B-2c.3_d~4"


# ------------------------------------------------------------------ REQ-CHATGPT-1


@pytest.mark.parametrize("redirect", [CHATGPT_STABLE, CHATGPT_CALLBACK], ids=["stable", "callback"])
def test_req_chatgpt_1_both_chatgpt_redirect_forms_register(tmp_path: Path, redirect: str) -> None:
    async def body(client: httpx2.AsyncClient) -> httpx2.Response:
        return await client.post(
            "/register",
            json={
                "redirect_uris": [redirect],
                "token_endpoint_auth_method": "none",
                "client_name": "ChatGPT",
            },
        )

    response = _run(_app(tmp_path), body)
    assert response.status_code == 201, response.text
    assert response.json()["redirect_uris"] == [redirect]


@pytest.mark.parametrize(
    "redirect",
    [
        "https://chatgpt.com.evil.example/x",
        "https://chatgpt.com.evil.example/connector/oauth/abc",
        "https://evil.example/connector/oauth/abc",
        "http://chatgpt.com/connector/oauth/abc",
        "http://chatgpt.com/connector_platform_oauth_redirect",
        "https://chatgpt.com:8443/connector/oauth/abc",
        "https://user@chatgpt.com/connector/oauth/abc",
        "https://chatgpt.com/connector/oauth/",
        "https://chatgpt.com/connector/oauth/abc/def",
        "https://chatgpt.com/connector/oauth/abc?next=https://evil.example",
        "https://chatgpt.com/connector/oauth/abc#x",
        "https://chatgpt.com/connector/oauth/..",
        "https://chatgpt.com/connector/oauth/.",
        "https://chatgpt.com/connector/oauth/%2e%2e",
        "https://chatgpt.com/connector/oauth/abc\n",
        "https://chatgpt.com/connector/oauth/" + "a" * 129,
        f"{CHATGPT_STABLE}/",
        f"{CHATGPT_STABLE}?x=1",
        "https://chatgpt.com/",
        "https://chat.openai.com/connector_platform_oauth_redirect",
    ],
)
def test_req_chatgpt_1_other_redirects_are_still_refused(redirect: str) -> None:
    assert allowed_redirect(redirect) is False


def test_req_chatgpt_1_registration_refuses_a_lookalike_host(tmp_path: Path) -> None:
    async def body(client: httpx2.AsyncClient) -> httpx2.Response:
        return await client.post(
            "/register",
            json={"redirect_uris": ["https://chatgpt.com.evil.example/x"], "client_name": "x"},
        )

    response = _run(_app(tmp_path), body)
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_redirect_uri"
    assert CHATGPT_STABLE in response.json()["error_description"]


def test_req_chatgpt_1_a_chatgpt_client_signs_in_and_reaches_mcp(tmp_path: Path) -> None:
    async def body(client: httpx2.AsyncClient) -> httpx2.Response:
        registered, callback = await _sign_in(client, CHATGPT_CALLBACK)
        issued = await _exchange(
            client, registered, _code_from(callback, CHATGPT_CALLBACK), redirect=CHATGPT_CALLBACK
        )
        assert issued.status_code == 200, issued.text
        return await _initialize(client, issued.json()["access_token"])

    served = _run(_app(tmp_path), body)
    assert served.status_code == 200, served.text


def test_req_chatgpt_1_a_client_cannot_use_another_chatgpt_callback(tmp_path: Path) -> None:
    async def body(client: httpx2.AsyncClient) -> httpx2.Response:
        registered = await _register(client, CHATGPT_CALLBACK)
        other = "https://chatgpt.com/connector/oauth/someone_else"
        return await _authorize(client, registered["client_id"], other)

    response = _run(_app(tmp_path), body)
    assert response.status_code == 400
    assert "github.com" not in response.headers.get("location", "")


# ------------------------------------------------------------------ REQ-CHATGPT-2


def test_req_chatgpt_2_metadata_has_s256_registration_and_auth_method_none(
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


# ------------------------------------------------------------------ REQ-CHATGPT-3


@pytest.mark.parametrize("resource", [BASE, f"{BASE}/"], ids=["exact", "trailing-slash"])
def test_req_chatgpt_3_resource_binds_the_token_to_the_mcp_audience(
    tmp_path: Path, resource: str
) -> None:
    async def body(client: httpx2.AsyncClient) -> tuple[str, httpx2.Response]:
        registered = await _register(client, CHATGPT_CALLBACK)
        start = await _authorize(
            client, registered["client_id"], CHATGPT_CALLBACK, resource=resource
        )
        assert start.status_code == 302, start.text
        state = _query(start.headers["location"])["state"]
        callback = await client.get(
            GITHUB_CALLBACK_PATH, params={"code": "gh-code", "state": state}
        )
        issued = await _exchange(
            client, registered, _code_from(callback, CHATGPT_CALLBACK), redirect=CHATGPT_CALLBACK
        )
        assert issued.status_code == 200, issued.text
        access = issued.json()["access_token"]
        return access, await _initialize(client, access)

    access, served = _run(_app(tmp_path), body)
    claims = jwt.decode(access, SIGNING_KEY, algorithms=["HS256"], audience=BASE)
    assert claims["aud"] == BASE
    assert served.status_code == 200, served.text


def test_req_chatgpt_3_authorize_for_another_resource_is_refused(tmp_path: Path) -> None:
    async def body(client: httpx2.AsyncClient) -> httpx2.Response:
        registered = await _register(client, CHATGPT_CALLBACK)
        return await _authorize(
            client,
            registered["client_id"],
            CHATGPT_CALLBACK,
            resource="https://elsewhere.example",
        )

    response = _run(_app(tmp_path), body)
    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith(CHATGPT_CALLBACK)
    assert _query(location)["error"] == "invalid_target"


@pytest.mark.parametrize(
    "audience",
    ["https://elsewhere.example", f"{BASE}.evil.example", "urn:lifescape:refresh"],
    ids=["other-origin", "lookalike-origin", "internal-audience"],
)
def test_req_chatgpt_3_a_token_for_another_audience_gets_401_on_mcp(
    tmp_path: Path, audience: str
) -> None:
    async def body(client: httpx2.AsyncClient) -> httpx2.Response:
        return await _initialize(client, _forged({"aud": audience}))

    response = _run(_app(tmp_path), body)
    assert response.status_code == 401
    assert "resource_metadata=" in response.headers["www-authenticate"]
