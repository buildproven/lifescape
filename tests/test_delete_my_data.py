"""BUI-2213: users can delete their data; the privacy page and the site say who can sign in.

REQ-DEL-1: a signed-in user's ``delete_my_data`` deletes every packet and run index that user
owns, leaves every other user's rows, and returns the counts. REQ-DEL-2: the privacy page says
that, with signup open, any GitHub account can sign in, that each user's packets and run indexes
are stored under their GitHub login, that only they can read them, and that ``delete_my_data``
removes them. REQ-DEL-3: the landing page "Use it in Claude" section and the README give the
hosted server URL as the way to connect from claude.ai, and keep the desktop extension as the
local option. The database runs on a SQLite stand-in; fixture records are synthetic.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

import anyio
from fastapi.testclient import TestClient
from mcp import Client
from mcp_types import CallToolResult
from test_landing_claude_section import _landing, _Section
from test_store_contract import _index, _packet
from test_store_per_user import (
    ALICE,
    BEARERS,
    BOB,
    OTHER_PACKET_ID,
    OWNER,
    PACKET_ID,
    RUN_ID,
    _client,
    _database,
    _hosted_app,
    _legacy_database,
    _NoSubject,
    _text,
)

from lifescape.mcp_server import create_server
from lifescape.store_db import DatabaseStore
from lifescape.web import create_app

ROOT = Path(__file__).parents[1]
HOSTED_URL = "https://lifescape.buildproven.ai/api/mcp"
MCPB_URL = "https://github.com/buildproven/lifescape/releases/latest/download/lifescape.mcpb"
TOOL = "delete_my_data"
THIRD_PACKET_ID = "e" * 12
OTHER_RUN_ID = "f" * 16


def _seed(store: DatabaseStore) -> None:
    """Alice owns two packets and two run indexes; Bob owns one of each, with Alice's run ID."""
    store.save_packet(_packet(PACKET_ID), user=ALICE)
    store.save_packet(_packet(THIRD_PACKET_ID), user=ALICE)
    store.save_run_index(_index(RUN_ID, "a"), user=ALICE)
    store.save_run_index(_index(OTHER_RUN_ID, "a"), user=ALICE)
    store.save_packet(_packet(OTHER_PACKET_ID), user=BOB)
    store.save_run_index(_index(RUN_ID, "b"), user=BOB)


def _hosted_call(tmp_path: Path, store: DatabaseStore, user: str, arguments: Any) -> Any:
    async def call() -> CallToolResult:
        async with _client(_hosted_app(tmp_path, store), BEARERS[user]) as client:
            return await client.call_tool(TOOL, arguments)

    return anyio.run(call)


def _tool_names(server: Any) -> set[str]:
    async def main() -> set[str]:
        async with Client(server, mode="legacy") as client:
            return {tool.name for tool in (await client.list_tools()).tools}

    return anyio.run(main)


# REQ-DEL-1 ----------------------------------------------------------------------------------


def test_req_del_1_store_deletes_only_that_users_rows_and_counts_them(tmp_path: Path) -> None:
    store = _database(tmp_path)
    _seed(store)

    deleted = store.delete_user_data(user=ALICE)

    assert (deleted.packets, deleted.run_indexes) == (2, 2)
    assert store.load_packets(user=ALICE) == ()
    assert store.get_run_index(RUN_ID, user=ALICE) is None
    assert store.get_run_index(OTHER_RUN_ID, user=ALICE) is None
    assert [item.packet.id for item in store.load_packets(user=BOB)] == [OTHER_PACKET_ID]
    assert store.get_run_index(RUN_ID, user=BOB) == _index(RUN_ID, "b")


def test_req_del_1_store_delete_for_a_user_with_no_rows_counts_zero(tmp_path: Path) -> None:
    store = _database(tmp_path)
    _seed(store)

    deleted = store.delete_user_data(user="carol")

    assert (deleted.packets, deleted.run_indexes) == (0, 0)
    assert len(store.load_packets(user=ALICE)) == 2
    assert len(store.load_packets(user=BOB)) == 1


def test_req_del_1_store_delete_with_no_user_leaves_signed_in_users_rows(tmp_path: Path) -> None:
    store = _database(tmp_path)
    _seed(store)
    store.save_packet(_packet("d" * 12))

    deleted = store.delete_user_data()

    assert (deleted.packets, deleted.run_indexes) == (1, 0)
    assert store.load_packets() == ()
    assert len(store.load_packets(user=ALICE)) == 2


def test_req_del_1_store_delete_first_migrates_a_legacy_database(tmp_path: Path) -> None:
    """A database made before rows had an owner migrates on the delete's first use: the old
    rows go to the owner setting, so the owner's delete removes them."""
    _legacy_database(tmp_path / "store.sqlite")
    store = _database(tmp_path, owner=OWNER)

    deleted = store.delete_user_data(user=OWNER)

    assert (deleted.packets, deleted.run_indexes) == (1, 1)
    with sqlite3.connect(tmp_path / "store.sqlite") as connection:
        left = connection.execute(
            "SELECT COUNT(*) FROM research_packets "
            "UNION ALL SELECT COUNT(*) FROM run_report_indexes"
        ).fetchall()
    assert left == [(0,), (0,)]


def test_req_del_1_hosted_delete_removes_the_callers_rows_and_returns_counts(
    tmp_path: Path,
) -> None:
    store = _database(tmp_path)
    _seed(store)

    result = _hosted_call(tmp_path, store, ALICE, {"confirm": True})

    assert not result.is_error, _text(result)
    assert result.structured_content == {
        "user": ALICE,
        "deleted_packets": 2,
        "deleted_run_indexes": 2,
    }
    assert store.load_packets(user=ALICE) == ()
    assert store.get_run_index(RUN_ID, user=ALICE) is None
    assert [item.packet.id for item in store.load_packets(user=BOB)] == [OTHER_PACKET_ID]
    assert store.get_run_index(RUN_ID, user=BOB) == _index(RUN_ID, "b")


def test_req_del_1_hosted_delete_called_twice_counts_zero_the_second_time(
    tmp_path: Path,
) -> None:
    store = _database(tmp_path)
    _seed(store)
    _hosted_call(tmp_path, store, BOB, {"confirm": True})

    again = _hosted_call(tmp_path, store, BOB, {"confirm": True})

    assert not again.is_error, _text(again)
    assert again.structured_content == {"user": BOB, "deleted_packets": 0, "deleted_run_indexes": 0}
    assert len(store.load_packets(user=ALICE)) == 2


def test_req_del_1_hosted_delete_without_confirm_deletes_nothing(tmp_path: Path) -> None:
    store = _database(tmp_path)
    _seed(store)

    refused = _hosted_call(tmp_path, store, ALICE, {"confirm": False})
    missing = _hosted_call(tmp_path, store, ALICE, {})

    assert refused.is_error
    assert "confirm" in _text(refused)
    assert missing.is_error
    assert len(store.load_packets(user=ALICE)) == 2
    assert store.get_run_index(RUN_ID, user=ALICE) == _index(RUN_ID, "a")


def test_req_del_1_hosted_token_without_a_subject_deletes_nothing(tmp_path: Path) -> None:
    store = _database(tmp_path)
    _seed(store)
    app = _hosted_app(tmp_path, store, _NoSubject())

    async def call() -> CallToolResult:
        async with _client(app, "any-bearer") as client:
            return await client.call_tool(TOOL, {"confirm": True})

    result = anyio.run(call)

    assert result.is_error
    assert "access token has no subject" in _text(result)
    assert len(store.load_packets(user=ALICE)) == 2
    assert len(store.load_packets(user=BOB)) == 1


def test_req_del_1_the_database_store_server_lists_and_runs_the_tool(tmp_path: Path) -> None:
    store = _database(tmp_path)
    server = create_server(output_dir=tmp_path / "output", store=store)

    assert TOOL in _tool_names(server)

    async def through_door() -> CallToolResult:
        async with Client(server, mode="legacy") as client:
            return await client.call_tool("run_tool", {})

    listed = anyio.run(through_door)
    assert isinstance(listed.structured_content, dict)
    names = {item["name"] for item in listed.structured_content["tools"]}
    assert TOOL in names


def test_req_del_1_the_local_file_server_has_no_delete_tool(tmp_path: Path) -> None:
    """The local server's records are the user's own files on their own computer."""
    assert TOOL not in _tool_names(create_server(output_dir=tmp_path))


# REQ-DEL-2 ----------------------------------------------------------------------------------


def _privacy(tmp_path: Path) -> str:
    with TestClient(
        create_app(tmp_path / "output", hosted_demo=True),
        base_url="https://lifescape.buildproven.ai",
    ) as client:
        page = client.get("/privacy")
    assert page.status_code == 200
    without_tags = re.sub(r"<[^>]+>", " ", page.text)
    return " ".join(without_tags.split())


def test_req_del_2_privacy_says_any_github_account_can_sign_in(tmp_path: Path) -> None:
    text = _privacy(tmp_path)

    assert HOSTED_URL in text
    assert "When signup is open, any GitHub account can sign in" in text


def test_req_del_2_privacy_says_records_are_stored_under_the_users_login(tmp_path: Path) -> None:
    text = _privacy(tmp_path)

    assert "stored under your GitHub login" in text
    assert "research packets" in text
    assert "run report indexes" in text
    assert "Only you can read them" in text


def test_req_del_2_privacy_says_delete_my_data_removes_them(tmp_path: Path) -> None:
    text = _privacy(tmp_path)
    section = text[text.index("Delete your data") :]

    assert TOOL in section
    assert "removes every research packet and run report index stored under your GitHub login" in (
        section
    )


# REQ-DEL-3 ----------------------------------------------------------------------------------


def _claude_section(tmp_path: Path) -> str:
    parser = _Section("claude-title")
    parser.feed(_landing(tmp_path / "output"))
    assert len(parser.sections) == 1
    return " ".join("".join(parser.sections[0]["text"]).split())


def test_req_del_3_landing_gives_the_hosted_url_for_claude_ai(tmp_path: Path) -> None:
    text = _claude_section(tmp_path)

    assert HOSTED_URL in text
    assert "claude.ai" in text
    assert "GitHub" in text
    assert TOOL in text
    assert "server you deploy yourself" not in text


def test_req_del_3_landing_keeps_the_desktop_extension_as_the_local_option(
    tmp_path: Path,
) -> None:
    text = _claude_section(tmp_path)

    assert "desktop extension" in text
    assert "lifescape.mcpb" in text
    assert text.index(HOSTED_URL) < text.index("desktop extension")


def _readme_first_section() -> str:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    headings = list(re.finditer(r"^## .+$", readme, flags=re.MULTILINE))
    return readme[headings[0].end() : headings[1].start()]


def test_req_del_3_readme_first_section_connects_claude_ai_to_the_hosted_url() -> None:
    section = _readme_first_section()
    connect = re.search(r"^### Connect from claude\.ai\n(.*?)(?=^### )", section, re.M | re.S)

    assert connect, "README has no 'Connect from claude.ai' section"
    body = connect.group(1)
    assert f"`{HOSTED_URL}`" in body
    assert "GitHub" in body
    assert f"`{TOOL}`" in body
    assert section.index("### Connect from claude.ai") < section.index(MCPB_URL)
    assert "desktop extension" in section[section.index(MCPB_URL) - 200 :]


def test_req_del_3_readme_hosted_section_says_any_github_account_can_sign_in() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    hosted = re.search(r"^## Hosted server.*?\n(.*?)(?=^### )", readme, re.M | re.S)

    assert hosted, "README has no hosted-server section"
    body = " ".join(hosted.group(1).split())
    assert "only the owner can sign in" not in body
    assert "any GitHub account can sign in" in body
    assert f"`{TOOL}`" in body
