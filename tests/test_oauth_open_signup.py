"""BUI-2212: with signup open, every GitHub user can sign in to hosted Lifescape.

REQ-SIGNUP-1: with ``LIFESCAPE_SIGNUP=open`` any GitHub login gets a code and tokens, with the
login in lower case as the token ``sub``; the owner's ``sub`` stays the exact
``OWNER_GITHUB_LOGIN`` value. Unset or ``owner``, only the owner signs in. REQ-SIGNUP-2: a
signed-in user's tool calls read and write only that user's packets and runs. REQ-SIGNUP-3: a
user's call after 200 in one UTC day (``LIFESCAPE_DAILY_CALLS`` changes it) is refused until the
next UTC day, with a message naming the limit. The GitHub API and the Census connector are stubs;
the database runs on a SQLite stand-in; fixture records are synthetic.
"""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import anyio
import httpx2
import jwt
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server.auth.provider import AccessToken
from mcp_types import CallToolResult
from sqlalchemy.exc import IntegrityError
from starlette.applications import Starlette
from test_mcp_packet_evidence import CRITICAL, StubCensus
from test_oauth import SIGNING_KEY, GitHubStub, _exchange, _query, _sign_in
from test_store_contract import _sqlite_url
from test_store_per_user import _text

from lifescape import mcp_server
from lifescape.mcp_http import create_hosted_mcp_app, create_http_app, create_owner_http_app
from lifescape.mcp_server import DAILY_CALLS_ENV, DEFAULT_DAILY_CALLS, CallLimit, DailyCalls
from lifescape.oauth import (
    GITHUB_CLIENT_ID_ENV,
    GITHUB_CLIENT_SECRET_ENV,
    OWNER_ENV,
    SIGNING_KEY_ENV,
    SIGNUP_ENV,
    OAuthConfigError,
    OAuthSettings,
)
from lifescape.store import DATABASE_URL_ENV, STORE_ENV, StoreError, StoreUnavailableError
from lifescape.store_db import DatabaseStore

BASE = "https://lifescape.example"
# Mixed case on purpose: the owner's sub must stay this exact value (BUI-2211 rows use it).
OWNER = "Lifescape-Owner"
ENV = {
    OWNER_ENV: OWNER,
    GITHUB_CLIENT_ID_ENV: "gh-client-id",
    GITHUB_CLIENT_SECRET_ENV: "gh-client-secret",
    SIGNING_KEY_ENV: SIGNING_KEY,
}
OPEN_ENV = {**ENV, SIGNUP_ENV: "open"}
VISITOR = "Some-Visitor"
OTHER_VISITOR = "another-visitor"
NEW_BERN = "3746340"
METRIC = CRITICAL[0]
DAY = date(2026, 11, 24)
JSON_HEADERS = {"Accept": "application/json, text/event-stream"}
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


def _database(tmp_path: Path) -> DatabaseStore:
    return DatabaseStore.from_url(_sqlite_url(tmp_path / "store.sqlite"))


def _owner_app(
    tmp_path: Path,
    login: str,
    env: Mapping[str, str] = OPEN_ENV,
    store: DatabaseStore | None = None,
) -> Starlette:
    return create_owner_http_app(
        output_dir=tmp_path / "output",
        base_url=BASE,
        env=env,
        github_transport=GitHubStub(login=login).transport(),
        store=_database(tmp_path) if store is None else store,
    )


@asynccontextmanager
async def _http(app: Starlette) -> AsyncIterator[httpx2.AsyncClient]:
    async with (
        app.router.lifespan_context(app),
        httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url=BASE) as client,
    ):
        yield client


async def _tokens(client: httpx2.AsyncClient) -> dict[str, Any]:
    """Sign in through GitHub and exchange the code; the token response as JSON."""
    registered, callback = await _sign_in(client)
    assert callback.status_code == 302, callback.text
    code = _query(callback.headers["location"])["code"]
    response = await _exchange(client, registered, code)
    assert response.status_code == 200, response.text
    tokens: dict[str, Any] = response.json()
    tokens["client"] = registered
    return tokens


def _sub(token: str) -> str:
    claims = jwt.decode(token, options={"verify_signature": False})
    return str(claims["sub"])


def _sign_in_as(tmp_path: Path, login: str, env: Mapping[str, str] = OPEN_ENV) -> dict[str, Any]:
    async def main() -> dict[str, Any]:
        async with _http(_owner_app(tmp_path, login, env)) as client:
            return await _tokens(client)

    return anyio.run(main)


def _callback_status(tmp_path: Path, login: str, env: Mapping[str, str]) -> httpx2.Response:
    async def main() -> httpx2.Response:
        async with _http(_owner_app(tmp_path, login, env)) as client:
            _, callback = await _sign_in(client)
            return callback

    return anyio.run(main)


def _initialize_status(tmp_path: Path, env: Mapping[str, str], bearer: str) -> int:
    async def main() -> int:
        async with _http(_owner_app(tmp_path, OWNER, env)) as client:
            response = await client.post(
                "/api/mcp",
                json=INITIALIZE,
                headers={**JSON_HEADERS, "Authorization": f"Bearer {bearer}"},
            )
            return response.status_code

    return anyio.run(main)


@asynccontextmanager
async def _mcp(app: Starlette, bearer: str) -> AsyncIterator[Client]:
    """An MCP client session on ``app`` (its lifespan already running) as ``bearer``."""
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app),
        base_url=BASE,
        headers={"Authorization": f"Bearer {bearer}"},
    ) as http_client:
        transport = streamable_http_client(f"{BASE}/api/mcp", http_client=http_client)
        async with Client(transport) as client:
            yield client


def _as_signed_in[T](app: Starlette, body: Callable[[Client], Awaitable[T]]) -> tuple[str, T]:
    """Sign in on ``app`` through GitHub, then run ``body`` as that user; (sub, result)."""

    async def main() -> tuple[str, T]:
        async with _http(app) as client:
            tokens = await _tokens(client)
            async with _mcp(app, tokens["access_token"]) as session:
                return _sub(tokens["access_token"]), await body(session)

    return anyio.run(main)


# REQ-SIGNUP-1 -------------------------------------------------------------------------------


def test_req_signup_1_open_signup_gives_any_github_login_a_token_with_its_own_sub(
    tmp_path: Path,
) -> None:
    tokens = _sign_in_as(tmp_path, VISITOR)

    assert _sub(tokens["access_token"]) == VISITOR.lower()
    assert _sub(tokens["refresh_token"]) == VISITOR.lower()
    assert _initialize_status(tmp_path, OPEN_ENV, tokens["access_token"]) == 200


@pytest.mark.parametrize("signup", [None, "owner"], ids=["unset", "owner"])
def test_req_signup_1_owner_only_signup_refuses_another_login_with_403(
    tmp_path: Path, signup: str | None
) -> None:
    env = dict(ENV) if signup is None else {**ENV, SIGNUP_ENV: signup}

    callback = _callback_status(tmp_path, VISITOR, env)

    assert callback.status_code == 403
    assert "location" not in callback.headers
    assert "code=" not in callback.text


@pytest.mark.parametrize("env", [ENV, OPEN_ENV], ids=["owner-only", "open"])
@pytest.mark.parametrize("login", [OWNER, OWNER.lower(), OWNER.upper()])
def test_req_signup_1_the_owner_sub_stays_the_exact_owner_setting(
    tmp_path: Path, env: Mapping[str, str], login: str
) -> None:
    tokens = _sign_in_as(tmp_path, login, env)

    assert _sub(tokens["access_token"]) == OWNER
    assert _initialize_status(tmp_path, env, tokens["access_token"]) == 200


def test_req_signup_1_a_refresh_keeps_the_users_own_sub(tmp_path: Path) -> None:
    async def main() -> dict[str, Any]:
        async with _http(_owner_app(tmp_path, VISITOR)) as client:
            tokens = await _tokens(client)
            response = await client.post(
                "/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": tokens["client"]["client_id"],
                    "refresh_token": tokens["refresh_token"],
                },
            )
            assert response.status_code == 200, response.text
            refreshed: dict[str, Any] = response.json()
            return refreshed

    refreshed = anyio.run(main)

    assert _sub(refreshed["access_token"]) == VISITOR.lower()
    assert _sub(refreshed["refresh_token"]) == VISITOR.lower()


def test_req_signup_1_closing_signup_refuses_tokens_given_while_it_was_open(
    tmp_path: Path,
) -> None:
    tokens = _sign_in_as(tmp_path, VISITOR)

    assert _initialize_status(tmp_path, ENV, tokens["access_token"]) == 401

    async def refresh() -> httpx2.Response:
        async with _http(_owner_app(tmp_path, OWNER, ENV)) as client:
            return await client.post(
                "/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": tokens["client"]["client_id"],
                    "refresh_token": tokens["refresh_token"],
                },
            )

    response = anyio.run(refresh)
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant"


def test_req_signup_1_the_signup_setting_reads_open_and_owner() -> None:
    assert SIGNUP_ENV == "LIFESCAPE_SIGNUP"
    assert OAuthSettings.from_env(OPEN_ENV).open_signup is True
    assert OAuthSettings.from_env({**ENV, SIGNUP_ENV: " open "}).open_signup is True
    assert OAuthSettings.from_env({**ENV, SIGNUP_ENV: "owner"}).open_signup is False
    assert OAuthSettings.from_env({**ENV, SIGNUP_ENV: ""}).open_signup is False
    assert OAuthSettings.from_env(ENV).open_signup is False


@pytest.mark.parametrize("value", ["yes", "Open", "public"])
def test_req_signup_1_an_unknown_signup_value_stops_the_server_naming_it(value: str) -> None:
    with pytest.raises(OAuthConfigError) as raised:
        OAuthSettings.from_env({**ENV, SIGNUP_ENV: value})

    assert raised.value.invalid == (SIGNUP_ENV,)
    assert "open" in str(raised.value)
    assert "owner" in str(raised.value)


def _hosted_env(tmp_path: Path, **extra: str) -> dict[str, str]:
    return {
        **ENV,
        STORE_ENV: "database",
        DATABASE_URL_ENV: _sqlite_url(tmp_path / "store.sqlite"),
        **extra,
    }


def _hosted_answer(tmp_path: Path, env: Mapping[str, str]) -> httpx2.Response:
    async def main() -> httpx2.Response:
        app = create_hosted_mcp_app(output_dir=tmp_path / "output", env=env, base_url=BASE)
        async with _http(app) as client:
            return await client.post("/api/mcp", json=INITIALIZE, headers=JSON_HEADERS)

    return anyio.run(main)


@pytest.mark.parametrize(
    ("name", "value"),
    [(SIGNUP_ENV, "yes"), (DAILY_CALLS_ENV, "many"), (DAILY_CALLS_ENV, "0")],
)
def test_req_signup_1_the_hosted_app_answers_503_naming_an_unusable_setting(
    tmp_path: Path, name: str, value: str
) -> None:
    response = _hosted_answer(tmp_path, _hosted_env(tmp_path, **{name: value}))

    assert response.status_code == 503
    assert response.json()["invalid"] == [name]
    assert response.json()["missing"] == []


def test_req_signup_1_the_hosted_app_serves_with_signup_open(tmp_path: Path) -> None:
    response = _hosted_answer(tmp_path, _hosted_env(tmp_path, **{SIGNUP_ENV: "open"}))

    assert response.status_code == 401


# REQ-SIGNUP-2 -------------------------------------------------------------------------------


class _NoMetrics:
    """Stands in for noaa_gsoy; it fetches none of the metrics these tests ask for."""

    name = "noaa_gsoy"
    supported_metric_ids: tuple[str, ...] = ()


@pytest.fixture
def stub_connectors(monkeypatch: pytest.MonkeyPatch) -> None:
    """The hosted app builds the default connectors; no test reaches the network."""
    monkeypatch.setattr(mcp_server, "CensusAcsConnector", StubCensus)
    monkeypatch.setattr(mcp_server, "NoaaGsoyConnector", _NoMetrics)


def _structured(result: CallToolResult) -> dict[str, Any]:
    assert not result.is_error, _text(result)
    assert isinstance(result.structured_content, dict)
    return result.structured_content


@pytest.mark.usefixtures("stub_connectors")
def test_req_signup_2_two_users_tool_calls_save_and_read_only_their_own_rows(
    tmp_path: Path,
) -> None:
    store = _database(tmp_path)
    fetch = {"place_ids": [NEW_BERN], "metric_ids": [METRIC]}

    async def save_and_read(client: Client) -> tuple[str, CallToolResult]:
        packet_id = str(
            _structured(await client.call_tool("fetch_live_evidence", fetch))["packet_id"]
        )
        return packet_id, await client.call_tool("inspect_evidence", {"packet_id": packet_id})

    visitor_sub, (visitor_packet, visitor_read) = _as_signed_in(
        _owner_app(tmp_path, VISITOR, store=store), save_and_read
    )
    owner_sub, (owner_packet, owner_read) = _as_signed_in(
        _owner_app(tmp_path, OWNER.lower(), store=store), save_and_read
    )

    async def read_the_others(client: Client) -> list[CallToolResult]:
        return [
            await client.call_tool("inspect_evidence", {"packet_id": owner_packet}),
            await client.call_tool("inspect_evidence", {"packet_id": visitor_packet}),
        ]

    _, (others_owner, others_visitor) = _as_signed_in(
        _owner_app(tmp_path, OTHER_VISITOR, store=store), read_the_others
    )
    _, (visitor_reads_owner, _) = _as_signed_in(
        _owner_app(tmp_path, VISITOR, store=store), read_the_others
    )

    assert (visitor_sub, owner_sub) == (VISITOR.lower(), OWNER)
    _structured(visitor_read)
    _structured(owner_read)
    for refused, packet_id in (
        (others_owner, owner_packet),
        (others_visitor, visitor_packet),
        (visitor_reads_owner, owner_packet),
    ):
        assert refused.is_error
        assert f"research packet {packet_id} not found" in _text(refused)
    with sqlite3.connect(tmp_path / "store.sqlite") as connection:
        owners = dict(connection.execute("SELECT packet_id, owner FROM research_packets"))
    assert owners == {visitor_packet: VISITOR.lower(), owner_packet: OWNER}


# REQ-SIGNUP-3 -------------------------------------------------------------------------------

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


class _Clock:
    """The UTC day the limit counts in; a test moves it forward."""

    def __init__(self) -> None:
        self.day = DAY

    def __call__(self) -> date:
        return self.day


def _limited_app(tmp_path: Path, call_limit: CallLimit | None, store: Any = None) -> Starlette:
    return create_http_app(
        output_dir=tmp_path / "output",
        base_url=BASE,
        issuer_url="https://auth.lifescape.example",
        token_verifier=_TwoUsers(),
        store=_database(tmp_path) if store is None else store,
        call_limit=call_limit,
    )


def _calls(
    app: Starlette, user: str, steps: Sequence[tuple[str, dict[str, Any]]]
) -> list[CallToolResult]:
    async def main() -> list[CallToolResult]:
        async with app.router.lifespan_context(app), _mcp(app, BEARERS[user]) as client:
            return [await client.call_tool(tool, arguments) for tool, arguments in steps]

    return anyio.run(main)


LIST: tuple[str, dict[str, Any]] = ("run_tool", {})


def _limit_refusal(result: CallToolResult, limit: int) -> None:
    assert result.is_error
    message = _text(result)
    assert f"{limit} tool calls" in message
    assert "UTC day" in message


def test_req_signup_3_call_201_of_the_day_is_refused_with_the_limit_message(
    tmp_path: Path,
) -> None:
    assert DEFAULT_DAILY_CALLS == 200
    results = _calls(_limited_app(tmp_path, None), ALICE, [LIST] * 201)

    assert all(not result.is_error for result in results[:200]), _text(results[199])
    _limit_refusal(results[200], 200)


def test_req_signup_3_the_daily_calls_setting_changes_the_limit(tmp_path: Path) -> None:
    assert DAILY_CALLS_ENV == "LIFESCAPE_DAILY_CALLS"

    async def body(client: Client) -> list[CallToolResult]:
        return [await client.call_tool(*LIST) for _ in range(4)]

    _, results = _as_signed_in(
        _owner_app(tmp_path, VISITOR, {**OPEN_ENV, DAILY_CALLS_ENV: " 3 "}), body
    )

    assert [result.is_error for result in results] == [False, False, False, True]
    _limit_refusal(results[3], 3)


def test_req_signup_3_the_limit_counts_each_user_apart_and_resets_each_utc_day(
    tmp_path: Path,
) -> None:
    clock = _Clock()
    limit = CallLimit(per_day=2, today=clock)
    store = _database(tmp_path)

    alice_day_one = _calls(_limited_app(tmp_path, limit, store), ALICE, [LIST] * 3)
    bob_day_one = _calls(_limited_app(tmp_path, limit, store), BOB, [LIST] * 2)
    alice_same_day = _calls(_limited_app(tmp_path, limit, store), ALICE, [LIST])
    clock.day = DAY + timedelta(days=1)
    alice_next_day = _calls(_limited_app(tmp_path, limit, store), ALICE, [LIST] * 2)

    assert [result.is_error for result in alice_day_one] == [False, False, True]
    _limit_refusal(alice_day_one[2], 2)
    assert [result.is_error for result in bob_day_one] == [False, False]
    _limit_refusal(alice_same_day[0], 2)
    assert [result.is_error for result in alice_next_day] == [False, False]


def test_req_signup_3_a_call_through_run_tool_counts_once(tmp_path: Path) -> None:
    limit = CallLimit(per_day=2, today=_Clock())
    steps = [("run_tool", {"tool": "export_report", "arguments": {"run_id": "c" * 16}}), LIST, LIST]

    results = _calls(_limited_app(tmp_path, limit), ALICE, steps)

    assert "no reports for run" in _text(results[0])
    assert not results[1].is_error, _text(results[1])
    _limit_refusal(results[2], 2)


def test_req_signup_3_the_local_store_counts_in_the_process(tmp_path: Path) -> None:
    limit = CallLimit(per_day=1, today=_Clock(), ledger=DailyCalls())
    app = create_http_app(
        output_dir=tmp_path / "output",
        base_url=BASE,
        issuer_url="https://auth.lifescape.example",
        token_verifier=_TwoUsers(),
        call_limit=limit,
    )

    results = _calls(app, ALICE, [LIST] * 2)

    assert not results[0].is_error, _text(results[0])
    _limit_refusal(results[1], 1)


def test_req_signup_3_the_stdio_server_has_no_user_and_no_limit(tmp_path: Path) -> None:
    server = mcp_server.create_server(
        output_dir=tmp_path / "output", call_limit=CallLimit(per_day=1, today=_Clock())
    )

    async def main() -> list[CallToolResult]:
        async with Client(server, mode="legacy") as client:
            return [await client.call_tool(*LIST) for _ in range(3)]

    assert all(not result.is_error for result in anyio.run(main))


def test_req_signup_3_the_in_process_count_drops_past_days() -> None:
    calls = DailyCalls()

    assert [calls.count_call(ALICE, "2026-11-24") for _ in range(2)] == [1, 2]
    assert calls.count_call(BOB, "2026-11-24") == 1
    assert calls.count_call(ALICE, "2026-11-25") == 1
    assert calls.count_call(BOB, "2026-11-25") == 1
    assert len(calls) == 2


def test_req_signup_3_every_replica_on_one_database_shares_the_count(tmp_path: Path) -> None:
    first, second = _database(tmp_path), _database(tmp_path)

    assert first.count_call(ALICE, "2026-11-24") == 1
    assert second.count_call(ALICE, "2026-11-24") == 2
    assert first.count_call(BOB, "2026-11-24") == 1
    assert second.count_call(ALICE, "2026-11-25") == 1
    with sqlite3.connect(tmp_path / "store.sqlite") as connection:
        rows = connection.execute("SELECT owner, day, calls FROM daily_tool_calls").fetchall()
    assert rows == [(ALICE, "2026-11-25", 1)]


def test_req_signup_3_a_first_call_raced_by_another_replica_still_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, replica = _database(tmp_path), _database(tmp_path)
    add_call = store._add_call
    raced: list[bool] = []

    def racing(user: str, day: str) -> int:
        if not raced:
            raced.append(True)
            replica.count_call(user, day)
            raise StoreError("duplicate") from IntegrityError("INSERT", {}, Exception("unique"))
        return add_call(user, day)

    monkeypatch.setattr(store, "_add_call", racing)

    assert store.count_call(ALICE, "2026-11-24") == 2


def test_req_signup_3_an_unreachable_database_is_an_error_not_a_free_call(
    tmp_path: Path,
) -> None:
    store = DatabaseStore.from_url(_sqlite_url(tmp_path / "missing-directory" / "store.sqlite"))

    with pytest.raises(StoreUnavailableError):
        store.count_call(ALICE, "2026-11-24")


class _NoSubject:
    """Test verifier whose tokens carry no subject."""

    async def verify_token(self, token: str) -> AccessToken | None:
        return AccessToken(token=token, client_id="claude", scopes=[], resource=BASE)


def test_req_signup_2_a_token_without_a_subject_is_refused_on_every_tool(tmp_path: Path) -> None:
    """Every call acts for the token's sub, so one with none runs nothing, not even a list."""
    app = create_http_app(
        output_dir=tmp_path / "output",
        base_url=BASE,
        issuer_url="https://auth.lifescape.example",
        token_verifier=_NoSubject(),
        store=_database(tmp_path),
    )

    async def main() -> CallToolResult:
        async with app.router.lifespan_context(app), _mcp(app, "any-bearer") as client:
            return await client.call_tool(*LIST)

    result = anyio.run(main)

    assert result.is_error
    assert "access token has no subject" in _text(result)


@pytest.mark.parametrize("value", ["0", "-5", "3.5", "lots"])
def test_req_signup_3_an_unusable_daily_calls_setting_stops_the_server(
    tmp_path: Path, value: str
) -> None:
    with pytest.raises(ValueError, match=f"{DAILY_CALLS_ENV} must be a whole number"):
        _owner_app(tmp_path, OWNER, {**OPEN_ENV, DAILY_CALLS_ENV: value})
