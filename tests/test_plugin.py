"""BUI-1905: the repo root is the Lifescape Claude plugin with its hosted server.

The marketplace lists the plugin by its source, so the root must hold a valid
``.claude-plugin/plugin.json``, a ``.mcp.json`` naming the hosted server (BUI-2010), and the
skill. Chat on web and mobile loads only remote http servers, so the plugin declares no local
command server and the skill gives no local path or install step.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tomllib
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import Client

from lifescape.mcp_http import HOSTED_ORIGIN, MCP_PATH
from lifescape.mcp_server import create_server

REPOSITORY = Path(__file__).resolve().parents[1]
PLUGIN = REPOSITORY / ".claude-plugin" / "plugin.json"
MCP_CONFIG = REPOSITORY / ".mcp.json"
SKILL = REPOSITORY / "skills" / "lifescape" / "SKILL.md"
HOSTED_URL = "https://lifescape.buildproven.ai/api/mcp"
TOOL_TOKEN = re.compile(r"`([a-z]+(?:_[a-z]+)+)`")
LOCAL_OR_INSTALL = (
    r"~/",
    r"/(?:Users|home)/",
    r"[A-Za-z]:\\",
    r"\.venv",
    r"\buvx?\b",
    r"\bpip\b",
    r"\bnpx?\b",
    r"claude mcp add",
    r"(?-i:lifescape mcp)",  # the stdio command, not "Lifescape MCP" the server
    r"desktop extension",
    r"\.mcpb",
    r"\binstall",
    r"\blocal(?:ly)?\b",
    r"on (?:this|the owner's|the user's|your) (?:computer|machine)",
    r"owner's computer",
)


def _json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{path.name} must hold a JSON object"
    return data


def _server_tool_names(tmp_path: Path) -> set[str]:
    async def main() -> set[str]:
        server = create_server(output_dir=tmp_path)
        async with Client(server, mode="legacy") as client:
            listed = await client.list_tools()
            return {tool.name for tool in listed.tools}

    return anyio.run(main)


def _claude_cli_missing() -> str | None:
    """Why ``claude plugin validate`` cannot run here, or None when it can."""
    claude = shutil.which("claude")
    if claude is None:
        return "the claude CLI is not on PATH"
    probe = subprocess.run(
        [claude, "--version"], capture_output=True, text=True, timeout=60, check=False
    )
    if probe.returncode != 0:
        return f"claude --version exited {probe.returncode}: {probe.stderr.strip()}"
    return None


def test_req_plugin_1_manifest_names_the_plugin_after_the_project() -> None:
    plugin = _json(PLUGIN)
    project = tomllib.loads((REPOSITORY / "pyproject.toml").read_text(encoding="utf-8"))

    assert plugin["name"] == "lifescape"
    assert plugin["name"] == project["project"]["name"]
    assert plugin["version"] == project["project"]["version"]
    assert isinstance(plugin["description"], str)
    assert plugin["description"].strip()


def test_req_plugin_1_claude_plugin_validate_passes_at_the_repo_root() -> None:
    missing = _claude_cli_missing()
    if missing is not None:
        pytest.skip(f"REQ-PLUGIN-1 claude plugin validate skipped: {missing}")
    claude = shutil.which("claude")
    assert claude is not None

    completed = subprocess.run(
        [claude, "plugin", "validate", "."],
        cwd=REPOSITORY,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    output = completed.stdout + completed.stderr

    assert completed.returncode == 0, output
    assert "Validation passed" in output, output
    # Without a manifest the validator still passes on the skills tree alone.
    assert "MANIFEST_NOT_FOUND" not in output, output


def test_req_plugin_2_mcp_config_declares_one_hosted_http_server() -> None:
    config = _json(MCP_CONFIG)

    assert list(config) == ["mcpServers"]
    servers = config["mcpServers"]
    assert servers == {"lifescape": {"type": "http", "url": HOSTED_URL}}
    # The URL is the one the hosted app serves (BUI-2010).
    assert f"{HOSTED_ORIGIN}{MCP_PATH}" == HOSTED_URL


def test_req_plugin_2_mcp_config_declares_no_local_command_server() -> None:
    text = MCP_CONFIG.read_text(encoding="utf-8")

    assert not re.search(r'"command"\s*:', text)
    assert not re.search(r'"args"\s*:', text)
    assert "stdio" not in text


def test_req_plugin_3_skill_names_exactly_the_hosted_tools(tmp_path: Path) -> None:
    text = SKILL.read_text(encoding="utf-8")
    named = set(TOOL_TOKEN.findall(text))

    assert named == _server_tool_names(tmp_path)


def test_req_plugin_3_skill_uses_the_hosted_connector() -> None:
    text = SKILL.read_text(encoding="utf-8")

    assert re.search(r"\bhosted\b", text, flags=re.IGNORECASE)
    assert "`lifescape` connector" in text


@pytest.mark.parametrize("pattern", LOCAL_OR_INSTALL)
def test_req_plugin_3_skill_has_no_local_path_or_install_step(pattern: str) -> None:
    text = SKILL.read_text(encoding="utf-8")

    match = re.search(pattern, text, flags=re.IGNORECASE)
    assert match is None, f"SKILL.md names a local path or install step: {match.group(0)!r}"


def test_req_plugin_4_manifest_names_the_hosted_privacy_policy() -> None:
    """BUI-2021: a remote plugin names its privacy policy (PRIVACY_URL_MISSING otherwise)."""
    plugin = _json(PLUGIN)

    assert plugin.get("privacyPolicyUrl") == f"{HOSTED_ORIGIN}/privacy"
    assert plugin["privacyPolicyUrl"] == "https://lifescape.buildproven.ai/privacy"
