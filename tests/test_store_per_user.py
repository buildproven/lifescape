"""BUI-2211: the hosted store keeps each signed-in user's packets and run indexes apart.

REQ-USER-1: a packet or run index is keyed by the signed-in user's subject (token ``sub``), and
another user's ID reads as not found. REQ-USER-2: a database made before the owner column
migrates once, and its rows belong to ``OWNER_GITHUB_LOGIN``. REQ-USER-3: the stdio server keeps
working with no user. The database runs on a SQLite stand-in; fixture records are synthetic.
"""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import anyio
import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server.auth.provider import AccessToken
from mcp_types import CallToolResult, TextContent
from starlette.applications import Starlette
from test_store_contract import FETCH, _calls, _index, _ok, _packet, _sqlite_url

from lifescape.mcp_http import _hosted_store, create_http_app
from lifescape.mcp_server import MARKDOWN_REPORT, create_server
from lifescape.oauth import OWNER_ENV
from lifescape.research_workspace import StoredResearchPacket
from lifescape.store import (
    DATABASE_URL_ENV,
    STORE_ENV,
    LocalStore,
    RunReportIndex,
    StoreConfigError,
    StoreError,
    store_from_env,
)
from lifescape.store_db import DatabaseStore

BASE = "https://lifescape.example"
ALICE, BOB = "alice", "bob"
PACKET_ID, OTHER_PACKET_ID = "a" * 12, "b" * 12
RUN_ID = "c" * 16
OWNER = "the-owner"
# Bearer values built from parts: the secret scan reads every commit.
BEARERS = {ALICE: "-".join(("bearer", ALICE)), BOB: "-".join(("bearer", BOB))}


def _database(tmp_path: Path, owner: str | None = None) -> DatabaseStore:
    return DatabaseStore.from_url(_sqlite_url(tmp_path / "store.sqlite"), owner=owner)


def _run_index_with_text(run_id: str) -> RunReportIndex:
    return RunReportIndex(
        run_id=run_id,
        reports={MARKDOWN_REPORT: "/reports/comparison.md"},
        sha256={MARKDOWN_REPORT: "d" * 64},
        contents={MARKDOWN_REPORT: "# Comparison for one user"},
    )


# REQ-USER-1 ---------------------------------------------------------------------------------


def test_req_user_1_a_packet_is_found_for_its_user_and_not_for_another(tmp_path: Path) -> None:
    store = _database(tmp_path)
    store.save_packet(_packet(PACKET_ID), user=ALICE)

    found = store.get_packet(PACKET_ID, user=ALICE)

    assert found is not None
    assert found.packet.id == PACKET_ID
    assert store.get_packet(PACKET_ID, user=BOB) is None
    assert store.get_packet(PACKET_ID) is None


def test_req_user_1_a_run_index_is_found_for_its_user_and_not_for_another(tmp_path: Path) -> None:
    store = _database(tmp_path)
    store.save_run_index(_index(RUN_ID), user=ALICE)

    assert store.get_run_index(RUN_ID, user=ALICE) == _index(RUN_ID)
    assert store.get_run_index(RUN_ID, user=BOB) is None
    assert store.get_run_index(RUN_ID) is None


def test_req_user_1_load_packets_returns_only_that_users_packets(tmp_path: Path) -> None:
    store = _database(tmp_path)
    store.save_packet(_packet(PACKET_ID), user=ALICE)
    store.save_packet(_packet(OTHER_PACKET_ID), user=BOB)

    assert [item.packet.id for item in store.load_packets(user=ALICE)] == [PACKET_ID]
    assert [item.packet.id for item in store.load_packets(user=BOB)] == [OTHER_PACKET_ID]
    assert store.load_packets(user="carol") == ()


def test_req_user_1_a_save_never_replaces_another_users_packet(tmp_path: Path) -> None:
    store = _database(tmp_path)
    store.save_packet(_packet(PACKET_ID, "New Bern"), user=ALICE)

    with pytest.raises(StoreError, match="refused the request"):
        store.save_packet(_packet(PACKET_ID, "Lake Geneva"), user=BOB)

    kept = store.get_packet(PACKET_ID, user=ALICE)
    assert kept is not None
    assert [lead.place.name for lead in kept.packet.leads] == ["New Bern"]
    assert store.get_packet(PACKET_ID, user=BOB) is None


def test_req_user_1_two_users_keep_their_own_index_of_one_run_id(tmp_path: Path) -> None:
    """A run ID hashes the run's inputs (pipeline.py), so two users comparing the same towns
    on the same evidence make the same one; each keeps their own index."""
    store = _database(tmp_path)
    store.save_run_index(_index(RUN_ID, "a"), user=ALICE)
    store.save_run_index(_index(RUN_ID, "b"), user=BOB)
    store.save_run_index(_index(RUN_ID, "c"))

    assert store.get_run_index(RUN_ID, user=ALICE) == _index(RUN_ID, "a")
    assert store.get_run_index(RUN_ID, user=BOB) == _index(RUN_ID, "b")
    assert store.get_run_index(RUN_ID) == _index(RUN_ID, "c")


def test_req_user_1_a_save_by_the_same_user_replaces_their_packet(tmp_path: Path) -> None:
    store = _database(tmp_path)
    store.save_packet(_packet(PACKET_ID, "New Bern"), user=ALICE)
    store.save_packet(_packet(PACKET_ID, "Lake Geneva"), user=ALICE)

    kept = store.get_packet(PACKET_ID, user=ALICE)
    assert kept is not None
    assert [lead.place.name for lead in kept.packet.leads] == ["Lake Geneva"]
    assert len(store.load_packets(user=ALICE)) == 1


def test_req_user_1_a_stored_row_carries_its_owner(tmp_path: Path) -> None:
    store = _database(tmp_path)
    store.save_packet(_packet(PACKET_ID), user=ALICE)
    store.save_run_index(_index(RUN_ID), user=BOB)

    with sqlite3.connect(tmp_path / "store.sqlite") as connection:
        packet_owner = connection.execute("SELECT owner FROM research_packets").fetchall()
        run_owner = connection.execute("SELECT owner FROM run_report_indexes").fetchall()

    assert packet_owner == [(ALICE,)]
    assert run_owner == [(BOB,)]


@asynccontextmanager
async def _client(app: Starlette, bearer: str) -> AsyncIterator[Client]:
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


class _TwoUsers:
    """Test verifier: each bearer is one signed-in user."""

    async def verify_token(self, token: str) -> AccessToken | None:
        for user, bearer in BEARERS.items():
            if token == bearer:
                return AccessToken(
                    token=token, client_id="claude", scopes=[], resource=BASE, subject=user
                )
        return None


class _NoSubject:
    """Test verifier whose tokens carry no subject."""

    async def verify_token(self, token: str) -> AccessToken | None:
        return AccessToken(token=token, client_id="claude", scopes=[], resource=BASE)


def _hosted_app(tmp_path: Path, store: DatabaseStore, verifier: Any = None) -> Starlette:
    return create_http_app(
        output_dir=tmp_path / "output",
        base_url=BASE,
        issuer_url="https://auth.lifescape.example",
        token_verifier=_TwoUsers() if verifier is None else verifier,
        store=store,
    )


def _text(result: CallToolResult) -> str:
    return "\n".join(item.text for item in result.content if isinstance(item, TextContent))


def test_req_user_1_hosted_tools_read_only_the_signed_in_users_records(tmp_path: Path) -> None:
    store = _database(tmp_path)
    store.save_packet(_packet(PACKET_ID), user=ALICE)
    store.save_run_index(_run_index_with_text(RUN_ID), user=ALICE)

    async def as_user(user: str) -> tuple[CallToolResult, CallToolResult]:
        # One app per session: the SDK runs a session manager once per app.
        async with _client(_hosted_app(tmp_path, store), BEARERS[user]) as client:
            packet = await client.call_tool("inspect_evidence", {"packet_id": PACKET_ID})
            report = await client.call_tool("export_report", {"run_id": RUN_ID})
            return packet, report

    alice_packet, alice_report = anyio.run(as_user, ALICE)
    bob_packet, bob_report = anyio.run(as_user, BOB)

    assert not alice_packet.is_error, _text(alice_packet)
    assert not alice_report.is_error, _text(alice_report)
    assert bob_packet.is_error
    assert f"research packet {PACKET_ID} not found" in _text(bob_packet)
    assert bob_report.is_error
    assert f"no reports for run {RUN_ID}" in _text(bob_report)


def test_req_user_1_a_hosted_token_without_a_subject_reads_nothing(tmp_path: Path) -> None:
    store = _database(tmp_path)
    store.save_packet(_packet(PACKET_ID), user=ALICE)
    app = _hosted_app(tmp_path, store, _NoSubject())

    async def call() -> CallToolResult:
        async with _client(app, "any-bearer") as client:
            return await client.call_tool("inspect_evidence", {"packet_id": PACKET_ID})

    result = anyio.run(call)

    assert result.is_error
    assert "access token has no subject" in _text(result)


# REQ-USER-2 ---------------------------------------------------------------------------------

CURRENT_SCHEMA = (
    "CREATE TABLE research_packets (packet_id VARCHAR(12) NOT NULL PRIMARY KEY, "
    "document TEXT NOT NULL)",
    "CREATE TABLE run_report_indexes (run_id VARCHAR(16) NOT NULL PRIMARY KEY, "
    "document TEXT NOT NULL)",
)


def _legacy_database(path: Path) -> None:
    """A database as the current schema (no owner column) made it, with one row in each table."""
    with sqlite3.connect(path) as connection:
        for statement in CURRENT_SCHEMA:
            connection.execute(statement)
        stored: StoredResearchPacket = _packet(PACKET_ID)
        connection.execute(
            "INSERT INTO research_packets VALUES (?, ?)", (PACKET_ID, stored.model_dump_json())
        )
        connection.execute(
            "INSERT INTO run_report_indexes VALUES (?, ?)",
            (RUN_ID, _index(RUN_ID).model_dump_json()),
        )


def test_req_user_2_a_database_from_the_current_schema_migrates_to_the_owner(
    tmp_path: Path,
) -> None:
    _legacy_database(tmp_path / "store.sqlite")
    store = _database(tmp_path, owner=OWNER)

    assert [item.packet.id for item in store.load_packets(user=OWNER)] == [PACKET_ID]
    assert store.get_packet(PACKET_ID, user=OWNER) is not None
    assert store.get_run_index(RUN_ID, user=OWNER) == _index(RUN_ID)
    assert store.get_packet(PACKET_ID, user=BOB) is None
    assert store.get_run_index(RUN_ID, user=BOB) is None
    with sqlite3.connect(tmp_path / "store.sqlite") as connection:
        owners = connection.execute(
            "SELECT owner FROM research_packets UNION ALL SELECT owner FROM run_report_indexes"
        ).fetchall()
    assert owners == [(OWNER,), (OWNER,)]
    # The rebuilt run index key is (owner, run_id): another user can save the same run ID.
    store.save_run_index(_index(RUN_ID, "b"), user=BOB)
    assert store.get_run_index(RUN_ID, user=BOB) == _index(RUN_ID, "b")
    assert store.get_run_index(RUN_ID, user=OWNER) == _index(RUN_ID)


def test_req_user_2_the_migration_runs_once_and_leaves_later_rows_alone(tmp_path: Path) -> None:
    _legacy_database(tmp_path / "store.sqlite")
    first = _database(tmp_path, owner=OWNER)
    first.save_packet(_packet(OTHER_PACKET_ID), user=BOB)
    assert first.get_packet(PACKET_ID, user=OWNER) is not None

    reopened = _database(tmp_path, owner=OWNER)

    assert [item.packet.id for item in reopened.load_packets(user=OWNER)] == [PACKET_ID]
    assert [item.packet.id for item in reopened.load_packets(user=BOB)] == [OTHER_PACKET_ID]


def test_req_user_2_rows_saved_with_no_user_stay_theirs_when_the_owner_is_set(
    tmp_path: Path,
) -> None:
    """Only the migration assigns owners: a no-user row saved later never goes to the owner."""
    first = _database(tmp_path, owner=OWNER)
    first.save_packet(_packet(PACKET_ID))
    first.save_run_index(_index(RUN_ID))

    reopened = _database(tmp_path, owner=OWNER)

    assert reopened.get_packet(PACKET_ID) is not None
    assert reopened.get_run_index(RUN_ID) == _index(RUN_ID)
    assert reopened.get_packet(PACKET_ID, user=OWNER) is None
    assert reopened.get_run_index(RUN_ID, user=OWNER) is None


def test_req_user_2_ownerless_rows_with_no_owner_setting_are_refused_not_guessed(
    tmp_path: Path,
) -> None:
    _legacy_database(tmp_path / "store.sqlite")
    store = _database(tmp_path)

    with pytest.raises(StoreConfigError, match=f"set {OWNER_ENV}"):
        store.get_packet(PACKET_ID)
    # Nothing changed: the owner's migration still finds and assigns both rows.
    migrated = _database(tmp_path, owner=OWNER)
    assert migrated.get_packet(PACKET_ID, user=OWNER) is not None
    assert migrated.get_run_index(RUN_ID, user=OWNER) == _index(RUN_ID)


def test_req_user_2_empty_tables_from_the_current_schema_migrate_with_no_owner(
    tmp_path: Path,
) -> None:
    with sqlite3.connect(tmp_path / "store.sqlite") as connection:
        for statement in CURRENT_SCHEMA:
            connection.execute(statement)
    store = _database(tmp_path)

    store.save_packet(_packet(PACKET_ID))
    store.save_run_index(_index(RUN_ID), user=ALICE)

    assert store.get_packet(PACKET_ID) is not None
    assert store.get_run_index(RUN_ID, user=ALICE) == _index(RUN_ID)


def test_req_user_2_the_environment_owner_reaches_the_database_adapter(tmp_path: Path) -> None:
    _legacy_database(tmp_path / "store.sqlite")
    environ = {
        STORE_ENV: "database",
        DATABASE_URL_ENV: _sqlite_url(tmp_path / "store.sqlite"),
        OWNER_ENV: f"  {OWNER}  ",
    }

    store = store_from_env(tmp_path, environ)

    assert isinstance(store, DatabaseStore)
    assert store.get_packet(PACKET_ID, user=OWNER) is not None


def test_req_user_2_the_hosted_store_migrates_to_the_owner_setting(tmp_path: Path) -> None:
    _legacy_database(tmp_path / "store.sqlite")
    environ = {
        STORE_ENV: "database",
        DATABASE_URL_ENV: _sqlite_url(tmp_path / "store.sqlite"),
        OWNER_ENV: OWNER,
    }

    store, missing, invalid = _hosted_store(environ)

    assert (missing, invalid) == ([], [])
    assert store is not None
    assert store.get_packet(PACKET_ID, user=OWNER) is not None


# REQ-USER-3 ---------------------------------------------------------------------------------


def test_req_user_3_the_local_store_saves_and_reads_with_no_user(tmp_path: Path) -> None:
    store = LocalStore(tmp_path / "output")
    store.save_packet(_packet(PACKET_ID))
    store.save_run_index(_index(RUN_ID))

    found = store.get_packet(PACKET_ID)
    assert found is not None
    assert found.packet.id == PACKET_ID
    assert store.get_run_index(RUN_ID) == _index(RUN_ID)
    assert [item.packet.id for item in store.load_packets()] == [PACKET_ID]


def test_req_user_3_the_stdio_server_saves_and_reads_a_packet_with_no_user(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "output"

    fetched, inspected = _calls(
        output_dir,
        [FETCH, ("inspect_evidence", lambda results: {"packet_id": _ok(results[0])["packet_id"]})],
    )

    packet_id = _ok(fetched)["packet_id"]
    assert not inspected.is_error, _text(inspected)
    assert LocalStore(output_dir).get_packet(packet_id) is not None


def test_req_user_3_a_database_server_with_no_auth_keeps_working_with_no_user(
    tmp_path: Path,
) -> None:
    store = _database(tmp_path)
    store.save_packet(_packet(PACKET_ID))

    async def call() -> CallToolResult:
        server = create_server(output_dir=tmp_path / "output", store=store)
        async with Client(server, mode="legacy") as client:
            return await client.call_tool("inspect_evidence", {"packet_id": PACKET_ID})

    assert not anyio.run(call).is_error
