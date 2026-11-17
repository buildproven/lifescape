"""BUI-1787: the Lifescape skill and the Claude Desktop extension package.

REQ-AGENT-1 checks the skill, REQ-AGENT-2 the desktop-extension manifest, and REQ-AGENT-3 the
README's first section.
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
import yaml
from mcp import Client

from lifescape.mcp_server import create_server

REPOSITORY = Path(__file__).resolve().parents[1]
SKILL = REPOSITORY / "skills" / "lifescape" / "SKILL.md"
MANIFEST = REPOSITORY / "manifest.json"
README = REPOSITORY / "README.md"
MCPB = "@anthropic-ai/mcpb@2.1.2"


def _skill_parts() -> tuple[dict[str, Any], str]:
    text = SKILL.read_text(encoding="utf-8")
    match = re.fullmatch(r"---\n(.*?)\n---\n(.*)", text, flags=re.DOTALL)
    assert match, "SKILL.md must start with YAML frontmatter between --- lines"
    frontmatter = yaml.safe_load(match.group(1))
    assert isinstance(frontmatter, dict)
    return frontmatter, match.group(2)


def _manifest() -> dict[str, Any]:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _mcpb_validate(path: Path) -> subprocess.CompletedProcess[str]:
    npx = shutil.which("npx")
    if npx is None:
        pytest.fail("npx is required to validate the desktop-extension manifest")
    return subprocess.run(
        [npx, "--yes", MCPB, "validate", str(path)],
        cwd=REPOSITORY,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def _server_tool_names(tmp_path: Path) -> set[str]:
    async def main() -> set[str]:
        server = create_server(output_dir=tmp_path)
        async with Client(server, mode="legacy") as client:
            listed = await client.list_tools()
            return {tool.name for tool in listed.tools}

    return anyio.run(main)


def _first_section(text: str) -> tuple[str, str]:
    headings = list(re.finditer(r"^## (.+)$", text, flags=re.MULTILINE))
    assert headings, "README has no ## section"
    first = headings[0]
    end = headings[1].start() if len(headings) > 1 else len(text)
    return first.group(1).strip(), text[first.end() : end]


def test_req_agent_1_skill_frontmatter_names_folder_and_short_description() -> None:
    frontmatter, _ = _skill_parts()

    assert frontmatter["name"] == SKILL.parent.name
    description = frontmatter["description"]
    assert isinstance(description, str)
    assert description.strip()
    assert len(description) <= 1024


def test_req_agent_1_skill_body_gives_workflow_and_rules() -> None:
    _, body = _skill_parts()
    lowered = body.lower()
    section = re.search(r"^## Workflow\n(.*?)(?=^## |\Z)", body, flags=re.MULTILINE | re.DOTALL)
    assert section, "SKILL.md needs a ## Workflow section"
    workflow = section.group(1).lower()

    steps = ["discover", "shortlist", "gather evidence", "owner approves", "compare finalists"]
    positions = [workflow.find(step) for step in steps]
    assert all(position >= 0 for position in positions), dict(zip(steps, positions, strict=True))
    assert positions == sorted(positions), "workflow steps must appear in order"
    for rule in (
        "preferences discover",
        "gates eliminate",
        "weights rank",
        "evidence decides",
        "tier c never scores",
        "never approve evidence",
        "never give a purchase recommendation",
    ):
        assert rule in lowered, rule


def test_req_agent_1_skill_tools_section_lists_exactly_the_server_tools(tmp_path: Path) -> None:
    _, body = _skill_parts()
    section = re.search(r"^## Tools\n(.*?)(?=^## |\Z)", body, flags=re.MULTILINE | re.DOTALL)
    assert section, "SKILL.md needs a ## Tools section"
    listed = re.findall(r"^- `([a-z_]+)`:", section.group(1), flags=re.MULTILINE)

    assert len(listed) == len(set(listed)), listed
    assert set(listed) == _server_tool_names(tmp_path)


def test_req_agent_2_manifest_runs_python_stdio_server() -> None:
    manifest = _manifest()
    server = manifest["server"]

    assert manifest["name"] == "lifescape"
    assert server["type"] == "uv"
    assert (REPOSITORY / server["entry_point"]).is_file()
    config = server["mcp_config"]
    assert config["command"] == "uv"
    assert config["args"][-2:] == ["lifescape", "mcp"]
    assert "${__dirname}" in config["args"]


def test_req_agent_2_manifest_matches_project_and_server_tools(tmp_path: Path) -> None:
    manifest = _manifest()
    project = tomllib.loads((REPOSITORY / "pyproject.toml").read_text("utf-8"))

    assert manifest["version"] == project["project"]["version"]
    assert manifest["compatibility"]["runtimes"]["python"] == project["project"]["requires-python"]
    assert {tool["name"] for tool in manifest["tools"]} == _server_tool_names(tmp_path)


def test_req_agent_2_mcpb_validate_passes() -> None:
    completed = _mcpb_validate(MANIFEST)

    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_req_agent_2_mcpb_validate_rejects_a_broken_manifest(tmp_path: Path) -> None:
    broken = _manifest()
    del broken["server"]
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(broken), encoding="utf-8")

    completed = _mcpb_validate(path)

    assert completed.returncode != 0, completed.stdout + completed.stderr


def test_req_agent_3_readme_starts_with_use_lifescape_in_claude() -> None:
    title, section = _first_section(README.read_text(encoding="utf-8"))

    assert title == "Use Lifescape in Claude"
    assert "Claude Desktop" in section
    assert ".mcpb" in section
    assert "Customize > Skills" in section
    assert "ZIP" in section
    assert "claude mcp add" in section
    assert "web UI is frozen" in section
