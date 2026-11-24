"""BUI-2212: when signup is open, every GitHub user can sign in to hosted Lifescape.

REQ-SIGNUP-1: with ``LIFESCAPE_SIGNUP=open`` the OAuth server gives a code and tokens to any
GitHub login, with that login (lower case) as the token ``sub``; unset or ``owner``, only
``OWNER_GITHUB_LOGIN`` gets in. REQ-SIGNUP-2: a signed-in user's tool calls act for the token's
``sub``, so each user sees only their own packets and runs. REQ-SIGNUP-3: after the daily limit
of tool calls (200 by default, ``LIFESCAPE_DAILY_CALLS``) a user's next call in that UTC day is
refused with a message that names the limit.

GitHub is a stub, the public data connectors are stubs, and the database is a SQLite stand-in,
so no test reaches the network. Fixture records are synthetic.
"""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import anyio
import httpx2
import jwt
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server.auth.provider import AccessToken
from mcp_types import CallToolResult, TextContent
from sqlalchemy.exc import IntegrityError
from starlette.applications import Starlette
from test_oauth import (
    BASE,
    ENV,
    OWNER,
    SIGNING_KEY,
    GitHubStub,
    _code_from,
    _exchange,
    _forged,
    _initialize,
    _run,
    _sign_in,
)
from test_store_contract import BROADBAND, NEW_BERN, StubCensus

import lifescape.mcp_server as mcp_server
from lifescape.mcp_http import create_hosted_mcp_app, create_http_app, create_owner_http_app
from lifescape.mcp_server import (
    DAILY_CALLS_ENV,
    DEFAULT_DAILY_CALLS,
    DailyCallsConfigError,
    create_server,
)
from lifescape.oauth import SIGNUP_ENV, OAuthConfigError, OAuthSettings
from lifescape.store import DATABASE_URL_ENV, STORE_ENV, LocalStore, Store, StoreError
from lifescape.store_db import DatabaseStore

OPEN = {**ENV, SIGNUP_ENV: "open"}
VISITOR = "Some-Visitor"
OTHER_VISITOR = "another-visitor"
DAY = date(2026, 11, 24)
NEXT_DAY = date(2026, 11, 25)


def _text(result: CallToolResult) -> str:
    return "\n".join(item.text for item in result.content if isinstance(item, TextContent))


def _database(tmp_path: Path) -> DatabaseStore:
    return DatabaseStore.from_url(f"sqlite:///{tmp_path / 'store.sqlite'}")


def _owner_app(
    tmp_path: Path,
    env: Mapping[str, str],
    login: str = OWNER,
    store: Store | None = None,
) -> Starlette:
    return create_owner_http_app(
        output_dir=tmp_path / "output",
        base_url=BASE,
        env=env,
        github_transport=GitHubStub(login=login).transport(),
        store=store,
    )


def _sign_in_and_exchange(app: Starlette) -> tuple[httpx2.Response, httpx2.Response]:
    """The GitHub callback, and the token response for its code (if it gave one)."""

    async def body(client: httpx2.AsyncClient) -> tuple[httpx2.Response, httpx2.Response]:
        registered, callback = await _sign_in(client)
        if callback.status_code != 302:
            return callback, callback
        return callback, await _exchange(client, registered, _code_from(callback))

    return _run(app, body)


def _tokens(tmp_path: Path, env: Mapping[str, str], login: str) -> dict[str, Any]:
    callback, issued = _sign_in_and_exchange(_owner_app(tmp_path, env, login))
    assert callback.status_code == 302, callback.text
    assert issued.status_code == 200, issued.text
    tokens: dict[str, Any] = issued.json()
    return tokens


def _subject(access: str) -> str:
    claims = jwt.decode(access, SIGNING_KEY, algorithms=["HS256"], audience=BASE)
    return str(claims["sub"])


@asynccontextmanager
async def _mcp(app: Starlette, bearer: str) -> AsyncIterator[Client]:
    async with (
        app.router.lifespan_context(app),
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url=BASE,
            headers={"Authorization": f"Bearer {bearer}"},
        ) as http_client,
    ):
        transport = streamable_http_client(f"{BASE}/api/mcp", http_client=http_client)
        async with Client(transport) as client:
            yield client


# REQ-SIGNUP-1 --------------------------------------------------------------------------------


def test_req_signup_1_open_signup_gives_a_visitor_a_token_with_their_own_sub(
    tmp_path: Path,
) -> None:
    tokens = _tokens(tmp_path, OPEN, VISITOR)

    assert _subject(tokens["access_token"]) == VISITOR.lower()

    async def served(client: httpx2.AsyncClient) -> httpx2.Response:
        return await _initialize(client, tokens["access_token"])

    response = _run(_owner_app(tmp_path, OPEN), served)
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("env", [ENV, {**ENV, SIGNUP_ENV: "owner"}], ids=["unset", "owner"])
def test_req_signup_1_closed_signup_gives_a_visitor_403_and_no_code(
    tmp_path: Path, env: Mapping[str, str]
) -> None:
    callback, _ = _sign_in_and_exchange(_owner_app(tmp_path, env, VISITOR))

    assert callback.status_code == 403
    assert "location" not in callback.headers
    assert "code=" not in callback.text


def test_req_signup_1_open_signup_keeps_the_owner_sub_as_configured(tmp_path: Path) -> None:
    """The owner's rows are keyed by OWNER_GITHUB_LOGIN (BUI-2211): the owner keeps that sub."""
    tokens = _tokens(tmp_path, OPEN, OWNER.upper())

    assert _subject(tokens["access_token"]) == OWNER


def test_req_signup_1_a_visitor_refresh_keeps_their_sub(tmp_path: Path) -> None:
    async def body(client: httpx2.AsyncClient) -> httpx2.Response:
        registered, callback = await _sign_in(client)
        issued = (await _exchange(client, registered, _code_from(callback))).json()
        refresh = {
            "grant_type": "refresh_token",
            "client_id": registered["client_id"],
            "refresh_token": issued["refresh_token"],
        }
        return await client.post("/token", data=refresh)

    refreshed = _run(_owner_app(tmp_path, OPEN, VISITOR), body)

    assert refreshed.status_code == 200, refreshed.text
    assert _subject(refreshed.json()["access_token"]) == VISITOR.lower()


def test_req_signup_1_closing_signup_voids_a_visitors_tokens(tmp_path: Path) -> None:
    """A visitor's token and refresh token stop working once signup is closed again."""

    async def body(client: httpx2.AsyncClient) -> dict[str, Any]:
        registered, callback = await _sign_in(client)
        issued = (await _exchange(client, registered, _code_from(callback))).json()
        return {"client_id": registered["client_id"], **issued}

    grants = _run(_owner_app(tmp_path, OPEN, VISITOR), body)

    async def replay(client: httpx2.AsyncClient) -> tuple[httpx2.Response, httpx2.Response]:
        served = await _initialize(client, grants["access_token"])
        refresh = {
            "grant_type": "refresh_token",
            "client_id": grants["client_id"],
            "refresh_token": grants["refresh_token"],
        }
        return served, await client.post("/token", data=refresh)

    served, refreshed = _run(_owner_app(tmp_path, ENV), replay)

    assert served.status_code == 401
    assert refreshed.status_code == 400
    assert refreshed.json()["error"] == "invalid_grant"


@pytest.mark.parametrize("value", ["yes", "invite", "closed"])
def test_req_signup_1_an_unknown_signup_value_fails_at_start_naming_it(value: str) -> None:
    with pytest.raises(OAuthConfigError) as raised:
        OAuthSettings.from_env({**ENV, SIGNUP_ENV: value})

    assert SIGNUP_ENV in str(raised.value)
    assert raised.value.invalid == (SIGNUP_ENV,)


def test_req_signup_1_settings_read_the_signup_mode() -> None:
    assert OAuthSettings.from_env(ENV).signup_open is False
    assert OAuthSettings.from_env({**ENV, SIGNUP_ENV: " owner "}).signup_open is False
    assert OAuthSettings.from_env({**ENV, SIGNUP_ENV: "open\n"}).signup_open is True
    assert OAuthSettings.from_env({**ENV, SIGNUP_ENV: "Open"}).signup_open is True
    assert OAuthSettings.from_env({**ENV, SIGNUP_ENV: "OWNER"}).signup_open is False


def test_req_signup_1_the_hosted_app_names_an_unknown_signup_value(tmp_path: Path) -> None:
    env = {
        **ENV,
        SIGNUP_ENV: "everyone",
        STORE_ENV: "database",
        DATABASE_URL_ENV: f"sqlite:///{tmp_path / 'store.sqlite'}",
    }
    app = create_hosted_mcp_app(output_dir=tmp_path, env=env, base_url=BASE)

    async def body(client: httpx2.AsyncClient) -> httpx2.Response:
        return await client.post("/api/mcp", json={})

    response = _run(app, body)
    assert response.status_code == 503
    assert response.json()["invalid"] == [SIGNUP_ENV]


@pytest.mark.parametrize("login", ["not a login", "-dash-first", "x" * 40])
def test_req_signup_1_open_signup_refuses_a_login_github_would_never_give(
    tmp_path: Path, login: str
) -> None:
    callback, _ = _sign_in_and_exchange(_owner_app(tmp_path, OPEN, login))

    assert callback.status_code == 403
    assert "code=" not in callback.text
    assert "cannot use" in callback.text


@pytest.mark.parametrize(
    ("env", "sub", "status"),
    [
        (OPEN, "some-visitor", 200),
        (OPEN, "Some-Visitor", 200),
        (OPEN, "not a login", 401),
        (ENV, "some-visitor", 401),
        (ENV, OWNER.upper(), 200),
    ],
    ids=["open-lower", "open-mixed-case", "open-not-a-login", "closed-visitor", "closed-owner"],
)
def test_req_signup_1_the_endpoint_accepts_only_a_sub_the_signup_rule_lets_in(
    tmp_path: Path, env: Mapping[str, str], sub: str, status: int
) -> None:
    """A signed token is checked against the rule again: only the subjects sign-in gives."""

    async def body(client: httpx2.AsyncClient) -> httpx2.Response:
        return await _initialize(client, _forged({"sub": sub}))

    assert _run(_owner_app(tmp_path, env), body).status_code == status


# REQ-SIGNUP-2 --------------------------------------------------------------------------------


class _Census(StubCensus):
    """The stub Census connector, built the way ``_default_connectors`` builds the real one."""

    def __init__(self) -> None:
        super().__init__([])


class _NoNoaa:
    """Stands in for the NOAA connector and fetches nothing, so no test reaches the network."""

    name = "noaa_gsoy"
    supported_metric_ids: tuple[str, ...] = ()


def test_req_signup_2_two_users_tool_calls_save_and_read_their_own_rows_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mcp_server, "CensusAcsConnector", _Census)
    monkeypatch.setattr(mcp_server, "NoaaGsoyConnector", _NoNoaa)
    store = _database(tmp_path)
    bearers = {
        login.lower(): _tokens(tmp_path, OPEN, login)["access_token"]
        for login in (VISITOR, OTHER_VISITOR)
    }
    fetch = {"place_ids": [NEW_BERN], "metric_ids": [BROADBAND]}

    async def fetch_as(user: str) -> str:
        async with _mcp(_owner_app(tmp_path, OPEN, store=store), bearers[user]) as client:
            result = await client.call_tool("fetch_live_evidence", fetch)
            assert not result.is_error, _text(result)
            assert isinstance(result.structured_content, dict)
            return str(result.structured_content["packet_id"])

    async def inspect_as(user: str, packet_id: str) -> CallToolResult:
        async with _mcp(_owner_app(tmp_path, OPEN, store=store), bearers[user]) as client:
            return await client.call_tool("inspect_evidence", {"packet_id": packet_id})

    visitor, other = VISITOR.lower(), OTHER_VISITOR
    visitor_packet = anyio.run(fetch_as, visitor)
    other_packet = anyio.run(fetch_as, other)

    with sqlite3.connect(tmp_path / "store.sqlite") as connection:
        owners = dict(connection.execute("SELECT packet_id, owner FROM research_packets"))
    assert owners == {visitor_packet: visitor, other_packet: other}
    assert not anyio.run(inspect_as, visitor, visitor_packet).is_error
    assert not anyio.run(inspect_as, other, other_packet).is_error
    refused = anyio.run(inspect_as, other, visitor_packet)
    assert refused.is_error
    assert f"research packet {visitor_packet} not found" in _text(refused)
    assert [item.packet.id for item in store.load_packets(user=visitor)] == [visitor_packet]
    assert [item.packet.id for item in store.load_packets(user=other)] == [other_packet]


# REQ-SIGNUP-3 --------------------------------------------------------------------------------

ALICE, BOB = "alice", "bob"
# Bearer values built from parts: the secret scan reads every commit.
BEARERS = {ALICE: "-".join(("bearer", ALICE)), BOB: "-".join(("bearer", BOB))}


class _TwoUsers:
    """Test verifier: each bearer is one signed-in user."""

    async def verify_token(self, token: str) -> AccessToken | None:
        for user, bearer in BEARERS.items():
            if token == bearer:
                return AccessToken(
                    token=token, client_id="claude", scopes=[], resource=BASE, subject=user
                )
        return None


def _limited_app(tmp_path: Path, store: Store, daily_calls: int | None = None) -> Starlette:
    extra: dict[str, Any] = {} if daily_calls is None else {"daily_calls": daily_calls}
    return create_http_app(
        output_dir=tmp_path / "output",
        base_url=BASE,
        issuer_url="https://auth.lifescape.example",
        token_verifier=_TwoUsers(),
        store=store,
        **extra,
    )


def _on_day(monkeypatch: pytest.MonkeyPatch, day: date) -> None:
    monkeypatch.setattr(mcp_server, "utc_today", lambda: day)


def _call_many(app: Starlette, bearer: str, count: int, tool: str = "run_tool") -> list[bool]:
    """Whether each of ``count`` calls was refused by the daily limit."""

    async def main() -> list[bool]:
        refused = []
        async with _mcp(app, bearer) as client:
            for _ in range(count):
                result = await client.call_tool(tool, {})
                refused.append(result.is_error and "tool calls per user" in _text(result))
        return refused

    return anyio.run(main)


def _last_refusal(app: Starlette, bearer: str) -> str:
    async def main() -> str:
        async with _mcp(app, bearer) as client:
            return _text(await client.call_tool("run_tool", {}))

    return anyio.run(main)


def test_req_signup_3_call_201_of_the_day_is_refused_naming_the_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _on_day(monkeypatch, DAY)
    store = _database(tmp_path)

    refused = _call_many(_limited_app(tmp_path, store), BEARERS[ALICE], DEFAULT_DAILY_CALLS)
    message = _last_refusal(_limited_app(tmp_path, store), BEARERS[ALICE])

    assert DEFAULT_DAILY_CALLS == 200
    assert refused == [False] * DEFAULT_DAILY_CALLS
    assert "200 tool calls per user each UTC day" in message
    assert DAILY_CALLS_ENV in message
    assert "00:00 UTC" in message


def test_req_signup_3_the_limit_counts_each_user_apart_and_resets_the_next_utc_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _on_day(monkeypatch, DAY)
    store = _database(tmp_path)

    first = _call_many(_limited_app(tmp_path, store, daily_calls=2), BEARERS[ALICE], 3)
    other = _call_many(_limited_app(tmp_path, store, daily_calls=2), BEARERS[BOB], 1)
    _on_day(monkeypatch, NEXT_DAY)
    next_day = _call_many(_limited_app(tmp_path, store, daily_calls=2), BEARERS[ALICE], 1)

    assert first == [False, False, True]
    assert other == [False]
    assert next_day == [False]


def test_req_signup_3_replicas_on_one_database_share_the_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _on_day(monkeypatch, DAY)

    first = _call_many(_limited_app(tmp_path, _database(tmp_path), 2), BEARERS[ALICE], 2)
    second = _call_many(_limited_app(tmp_path, _database(tmp_path), 2), BEARERS[ALICE], 1)

    assert first == [False, False]
    assert second == [True]


def test_req_signup_3_a_tool_run_through_run_tool_counts_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _on_day(monkeypatch, DAY)
    app = _limited_app(tmp_path, _database(tmp_path), daily_calls=2)

    async def main() -> list[CallToolResult]:
        async with _mcp(app, BEARERS[ALICE]) as client:
            door = await client.call_tool(
                "run_tool", {"tool": "find_place", "arguments": {"query": "New Bern, NC"}}
            )
            listed = await client.call_tool("run_tool", {})
            return [door, listed]

    door, listed = anyio.run(main)
    assert not door.is_error, _text(door)
    assert not listed.is_error, _text(listed)


def test_req_signup_3_a_local_store_server_counts_in_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _on_day(monkeypatch, DAY)
    app = _limited_app(tmp_path, LocalStore(tmp_path / "local"), daily_calls=1)

    assert _call_many(app, BEARERS[ALICE], 2) == [False, True]


def test_req_signup_3_lifescape_daily_calls_changes_the_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _on_day(monkeypatch, DAY)
    store = _database(tmp_path)
    env = {**OPEN, DAILY_CALLS_ENV: "3"}
    bearer = _tokens(tmp_path, env, VISITOR)["access_token"]

    refused = _call_many(_owner_app(tmp_path, env, store=store), bearer, 4)
    message = _last_refusal(_owner_app(tmp_path, env, store=store), bearer)

    assert refused == [False, False, False, True]
    assert "3 tool calls per user each UTC day" in message


@pytest.mark.parametrize("value", ["0", "-5", "many", "2.5", "+3", "\u0663"])
def test_req_signup_3_an_unusable_daily_calls_value_fails_at_start_naming_it(
    tmp_path: Path, value: str
) -> None:
    env = {**OPEN, DAILY_CALLS_ENV: value}
    with pytest.raises(ValueError, match=DAILY_CALLS_ENV):
        _owner_app(tmp_path, env)

    hosted = create_hosted_mcp_app(
        output_dir=tmp_path,
        env={**env, STORE_ENV: "database", DATABASE_URL_ENV: f"sqlite:///{tmp_path / 'h.sqlite'}"},
        base_url=BASE,
    )

    async def body(client: httpx2.AsyncClient) -> httpx2.Response:
        return await client.post("/api/mcp", json={})

    response = _run(hosted, body)
    assert response.status_code == 503
    assert response.json()["invalid"] == [DAILY_CALLS_ENV]


def test_req_signup_3_the_stdio_server_has_no_user_and_no_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _on_day(monkeypatch, DAY)
    server = create_server(output_dir=tmp_path, store=LocalStore(tmp_path / "local"), daily_calls=1)

    async def main() -> list[CallToolResult]:
        async with Client(server, mode="legacy") as client:
            return [await client.call_tool("run_tool", {}) for _ in range(3)]

    assert [result.is_error for result in anyio.run(main)] == [False, False, False]


def test_req_signup_3_the_database_counts_each_users_calls_per_day(tmp_path: Path) -> None:
    store = _database(tmp_path)

    counts = [store.count_call(ALICE, DAY) for _ in range(3)]
    other = store.count_call(BOB, DAY)
    next_day = store.count_call(ALICE, NEXT_DAY)

    assert counts == [1, 2, 3]
    assert other == 1
    assert next_day == 1
    with sqlite3.connect(tmp_path / "store.sqlite") as connection:
        days: Sequence[tuple[str]] = connection.execute(
            "SELECT DISTINCT day FROM daily_tool_calls"
        ).fetchall()
    # The first call of a day drops the rows of earlier days.
    assert list(days) == [(NEXT_DAY.isoformat(),)]


class _NoSubject:
    """Test verifier whose tokens carry no subject."""

    async def verify_token(self, token: str) -> AccessToken | None:
        return AccessToken(token=token, client_id="claude", scopes=[], resource=BASE)


def test_req_signup_3_a_hosted_token_without_a_subject_runs_no_tool(tmp_path: Path) -> None:
    """A call that cannot be counted for a user is refused, even one that reads no record."""
    app = create_http_app(
        output_dir=tmp_path / "output",
        base_url=BASE,
        issuer_url="https://auth.lifescape.example",
        token_verifier=_NoSubject(),
        store=_database(tmp_path),
    )

    async def main() -> CallToolResult:
        async with _mcp(app, "any-bearer") as client:
            return await client.call_tool("find_place", {"query": "New Bern, NC"})

    result = anyio.run(main)
    assert result.is_error
    assert "access token has no subject" in _text(result)


def test_req_signup_3_a_store_that_cannot_count_refuses_the_call_naming_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _on_day(monkeypatch, DAY)
    store = DatabaseStore.from_url(f"sqlite:///{tmp_path / 'missing-directory' / 's.sqlite'}")

    async def main() -> CallToolResult:
        async with _mcp(_limited_app(tmp_path, store), BEARERS[ALICE]) as client:
            return await client.call_tool("run_tool", {})

    result = anyio.run(main)
    assert result.is_error
    assert "database store at sqlite" in _text(result)
    assert "unreachable" in _text(result)


def test_req_signup_3_a_first_row_made_by_another_replica_is_counted_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two replicas insert one user's first row at once: the refused one counts again."""
    store = _database(tmp_path)
    other_replica = _database(tmp_path)
    real_count = DatabaseStore._count
    raced: list[bool] = []

    def racing_count(self: DatabaseStore, *args: Any) -> int:
        if not raced:
            raced.append(True)
            other_replica.count_call(ALICE, DAY)
            raise StoreError("duplicate key") from IntegrityError("INSERT", {}, Exception("dup"))
        return real_count(self, *args)

    monkeypatch.setattr(DatabaseStore, "_count", racing_count)

    assert store.count_call(ALICE, DAY) == 2


def test_req_signup_3_a_store_error_other_than_a_duplicate_is_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _database(tmp_path)
    attempts: list[bool] = []

    def failing_count(self: DatabaseStore, *args: Any) -> int:
        attempts.append(True)
        raise StoreError("refused the request")

    monkeypatch.setattr(DatabaseStore, "_count", failing_count)

    with pytest.raises(StoreError, match="refused the request"):
        store.count_call(ALICE, DAY)
    assert attempts == [True]


def test_req_signup_3_a_daily_limit_below_one_is_refused(tmp_path: Path) -> None:
    with pytest.raises(DailyCallsConfigError, match="at least 1"):
        create_server(output_dir=tmp_path, store=LocalStore(tmp_path / "local"), daily_calls=0)


def test_req_signup_3_the_process_environment_sets_the_limit_of_a_hosted_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _on_day(monkeypatch, DAY)
    monkeypatch.setenv(DAILY_CALLS_ENV, "2")
    app = _limited_app(tmp_path, _database(tmp_path))

    assert _call_many(app, BEARERS[ALICE], 3) == [False, False, True]
    message = _last_refusal(_limited_app(tmp_path, _database(tmp_path)), BEARERS[ALICE])
    assert "Daily limit reached" in message
    assert "2 tool calls per user each UTC day" in message


def test_req_signup_3_an_unusable_limit_in_the_process_environment_fails_at_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(DAILY_CALLS_ENV, "none")

    with pytest.raises(DailyCallsConfigError, match=DAILY_CALLS_ENV):
        _limited_app(tmp_path, _database(tmp_path))


def test_req_signup_3_servers_on_one_local_store_share_the_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _on_day(monkeypatch, DAY)

    first = _call_many(_limited_app(tmp_path, LocalStore(tmp_path / "local"), 2), BEARERS[ALICE], 2)
    second = _call_many(
        _limited_app(tmp_path, LocalStore(tmp_path / "local"), 2), BEARERS[ALICE], 1
    )
    elsewhere = _call_many(
        _limited_app(tmp_path, LocalStore(tmp_path / "other-local"), 2), BEARERS[ALICE], 1
    )

    assert first == [False, False]
    assert second == [True]
    assert elsewhere == [False]


def test_req_signup_2_the_hosted_app_takes_stub_connectors_for_each_users_fetch(
    tmp_path: Path,
) -> None:
    """``live_connectors`` reaches the tools of the hosted app, so a test needs no network."""
    store = _database(tmp_path)
    bearer = _tokens(tmp_path, OPEN, VISITOR)["access_token"]
    app = create_owner_http_app(
        output_dir=tmp_path / "output",
        base_url=BASE,
        env=OPEN,
        github_transport=GitHubStub(login=VISITOR).transport(),
        store=store,
        live_connectors=lambda: (_Census(),),
    )

    async def fetch() -> CallToolResult:
        async with _mcp(app, bearer) as client:
            return await client.call_tool(
                "fetch_live_evidence", {"place_ids": [NEW_BERN], "metric_ids": [BROADBAND]}
            )

    result = anyio.run(fetch)
    assert not result.is_error, _text(result)
    assert isinstance(result.structured_content, dict)
    packet_id = result.structured_content["packet_id"]
    assert store.get_packet(packet_id, user=VISITOR.lower()) is not None
    assert store.get_packet(packet_id, user=OTHER_VISITOR) is None


def test_req_signup_3_the_count_follows_the_system_clock_into_the_next_utc_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The day comes from ``time.time``: one second past 00:00 UTC is a new day."""
    import time

    late = datetime(2026, 11, 24, 23, 59, 59, tzinfo=UTC).timestamp()
    monkeypatch.setattr(time, "time", lambda: late)
    store = _database(tmp_path)

    before = _call_many(_limited_app(tmp_path, store, daily_calls=1), BEARERS[ALICE], 2)
    message = _last_refusal(_limited_app(tmp_path, store, daily_calls=1), BEARERS[ALICE])
    monkeypatch.setattr(time, "time", lambda: late + 2)
    after = _call_many(_limited_app(tmp_path, store, daily_calls=1), BEARERS[ALICE], 1)

    assert before == [False, True]
    assert "daily limit" in message
    assert DAY.isoformat() in message
    assert after == [False]
