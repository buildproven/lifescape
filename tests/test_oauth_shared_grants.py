"""BUI-2027: spent OAuth codes and refresh tokens are shared across replicas.

Vercel runs the hosted endpoint as many instances. Each test builds two owner apps (two
"replicas") over one database file, as two instances over one Neon database would be, and
checks that a code or refresh token spent on one replica is refused by the other.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

import anyio
import httpx2
import pytest
from starlette.applications import Starlette
from test_oauth import (
    BASE,
    ENV,
    GitHubStub,
    _code_from,
    _exchange,
    _http,
    _sign_in,
)

from lifescape.mcp_http import create_hosted_mcp_app, create_owner_http_app
from lifescape.store import DATABASE_URL_ENV, STORE_ENV, StoreUnavailableError
from lifescape.store_db import DatabaseStore


def _database(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'shared.sqlite'}"


def _replica(tmp_path: Path, url: str) -> Starlette:
    """One instance: its own process state, the shared database store."""
    return create_owner_http_app(
        output_dir=tmp_path,
        base_url=BASE,
        env=ENV,
        github_transport=GitHubStub().transport(),
        store=DatabaseStore.from_url(url),
    )


def _two_replicas[T](
    first: Starlette,
    second: Starlette,
    body: Callable[[httpx2.AsyncClient, httpx2.AsyncClient], Awaitable[T]],
) -> T:
    """Run ``body`` with one client on each replica, both lifespans started."""

    async def main() -> T:
        async with _http(first) as one, _http(second) as two:
            return await body(one, two)

    return anyio.run(main)


def test_bui_2027_req_1_a_code_spent_on_one_replica_is_refused_by_another(
    tmp_path: Path,
) -> None:
    url = _database(tmp_path)

    async def body(
        one: httpx2.AsyncClient, two: httpx2.AsyncClient
    ) -> tuple[httpx2.Response, httpx2.Response]:
        registered, callback = await _sign_in(one)
        code = _code_from(callback)
        return await _exchange(one, registered, code), await _exchange(two, registered, code)

    first, replay = _two_replicas(_replica(tmp_path, url), _replica(tmp_path, url), body)
    assert first.status_code == 200, first.text
    assert replay.status_code == 400
    assert replay.json()["error"] == "invalid_grant"
    assert "access_token" not in replay.json()


def test_bui_2027_req_1_a_refresh_token_spent_on_one_replica_is_refused_by_another(
    tmp_path: Path,
) -> None:
    url = _database(tmp_path)

    async def body(
        one: httpx2.AsyncClient, two: httpx2.AsyncClient
    ) -> tuple[httpx2.Response, httpx2.Response]:
        registered, callback = await _sign_in(one)
        tokens = (await _exchange(one, registered, _code_from(callback))).json()
        form = {
            "grant_type": "refresh_token",
            "client_id": registered["client_id"],
            "refresh_token": tokens["refresh_token"],
        }
        return await one.post("/token", data=form), await two.post("/token", data=form)

    refreshed, replay = _two_replicas(_replica(tmp_path, url), _replica(tmp_path, url), body)
    assert refreshed.status_code == 200, refreshed.text
    assert replay.status_code == 400
    assert replay.json()["error"] == "invalid_grant"


def test_bui_2027_req_1_the_hosted_app_shares_spent_grants_through_its_database(
    tmp_path: Path,
) -> None:
    """The Vercel entry (``create_hosted_mcp_app``) wires the shared record, not a local one."""
    env = {**ENV, STORE_ENV: "database", DATABASE_URL_ENV: _database(tmp_path)}

    def hosted() -> Starlette:
        return create_hosted_mcp_app(
            output_dir=tmp_path,
            env=env,
            base_url=BASE,
            github_transport=GitHubStub().transport(),
        )

    async def body(
        one: httpx2.AsyncClient, two: httpx2.AsyncClient
    ) -> tuple[httpx2.Response, httpx2.Response]:
        registered, callback = await _sign_in(one)
        code = _code_from(callback)
        return await _exchange(one, registered, code), await _exchange(two, registered, code)

    first, replay = _two_replicas(hosted(), hosted(), body)
    assert first.status_code == 200, first.text
    assert replay.status_code == 400
    assert replay.json()["error"] == "invalid_grant"


def test_bui_2027_req_1_the_database_claims_each_grant_once_across_stores(
    tmp_path: Path,
) -> None:
    url = _database(tmp_path)
    one, two = DatabaseStore.from_url(url), DatabaseStore.from_url(url)
    later = time.time() + 60

    assert one.claim_grant("code:a", later) is True
    assert two.claim_grant("code:a", later) is False
    assert one.claim_grant("code:a", later) is False
    # A different grant, and the same ID under another kind, are separate claims.
    assert two.claim_grant("code:b", later) is True
    assert two.claim_grant("refresh:a", later) is True


def test_bui_2027_req_1_expired_grants_are_dropped_from_the_database(tmp_path: Path) -> None:
    url = _database(tmp_path)
    one, two = DatabaseStore.from_url(url), DatabaseStore.from_url(url)

    assert one.claim_grant("code:old", time.time() - 1) is True
    assert one.claim_grant("code:new", time.time() + 60) is True
    # The next claim on any store prunes the expired row; the live one stays spent.
    assert two.claim_grant("code:other", time.time() + 60) is True
    with sqlite3.connect(tmp_path / "shared.sqlite") as connection:
        rows = connection.execute("SELECT grant_id FROM spent_oauth_grants").fetchall()
    assert sorted(row[0] for row in rows) == ["code:new", "code:other"]
    assert two.claim_grant("code:new", time.time() + 60) is False


def test_bui_2027_req_1_an_unreachable_record_refuses_the_grant(tmp_path: Path) -> None:
    """Without the shared record a replay cannot be told from a first use: no token."""
    missing = tmp_path / "no-such-directory" / "shared.sqlite"
    store = DatabaseStore.from_url(f"sqlite:///{missing}")

    with pytest.raises(StoreUnavailableError, match="database store"):
        store.claim_grant("code:a", time.time() + 60)


def test_bui_2027_req_1_a_database_from_before_the_ledger_gains_it_on_first_claim(
    tmp_path: Path,
) -> None:
    """A deployed database holds only the record tables; the first claim adds the grant table."""
    path = tmp_path / "shared.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE research_packets (packet_id VARCHAR(12) PRIMARY KEY, document TEXT)"
        )
    store = DatabaseStore.from_url(f"sqlite:///{path}")

    assert store.claim_grant("code:a", time.time() + 60) is True
    assert store.claim_grant("code:a", time.time() + 60) is False


def test_bui_2027_req_1_a_failed_insert_is_an_error_not_a_spent_grant(tmp_path: Path) -> None:
    """Only a duplicate key reads as "already spent"; any other insert failure raises."""
    path = tmp_path / "shared.sqlite"
    store = DatabaseStore.from_url(f"sqlite:///{path}")
    assert store.claim_grant("code:a", time.time() + 60) is True
    with sqlite3.connect(path) as connection:
        # Fires on insert only, after the prune succeeds, and fails with "no such table".
        connection.execute(
            "CREATE TRIGGER break_insert BEFORE INSERT ON spent_oauth_grants "
            "BEGIN INSERT INTO no_such_table VALUES (1); END"
        )

    with pytest.raises(StoreUnavailableError, match="no such table"):
        store.claim_grant("code:b", time.time() + 60)
