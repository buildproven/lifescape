"""BUI-2072: README leads with the free install anyone can use, and the release is 1.2.0."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
CHANGELOG = ROOT / "CHANGELOG.md"

RELEASE = "https://github.com/buildproven/lifescape/releases/latest/download"
MCPB_URL = f"{RELEASE}/lifescape.mcpb"
SKILL_URL = f"{RELEASE}/lifescape-skill.zip"
VERSION = "1.2.0"


def _sections(text: str, level: str) -> list[tuple[str, str]]:
    """Split markdown into (title, body) pairs at headings of exactly ``level`` hashes."""
    headings = list(re.finditer(rf"^{level} (.+)$", text, flags=re.MULTILINE))
    assert headings, f"README has no {level} heading"
    parts = []
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        parts.append((heading.group(1).strip(), text[heading.end() : end]))
    return parts


def _anchor(title: str) -> str:
    """GitHub's heading anchor: lowercase, drop punctuation, spaces to hyphens."""
    return re.sub(r"[^a-z0-9 _-]", "", title.lower()).replace(" ", "-")


def _readme() -> str:
    return README.read_text(encoding="utf-8")


def _first_section() -> str:
    return _sections(_readme(), "##")[0][1]


def _hosted_section() -> tuple[str, str]:
    hosted = [(t, b) for t, b in _sections(_readme(), "###?") if "hosted" in t.lower()]
    assert hosted, "README has no hosted-server section"
    return hosted[0]


def test_req_free_1_first_section_downloads_both_release_assets() -> None:
    section = _first_section()
    assert MCPB_URL in section
    assert SKILL_URL in section
    assert "Claude Desktop" in section


def test_req_free_1_first_section_has_no_private_marketplace_install() -> None:
    section = _first_section()
    assert "buildproven/claude-plugins" not in section
    assert "/plugin install" not in section
    assert "Add marketplace" not in section


def test_req_free_1_first_section_gives_the_web_app_and_cli_after_the_extension() -> None:
    section = _first_section()
    extension = section.index(MCPB_URL)
    web_app = section.find("lifescape app")
    cli = section.find("lifescape run")
    assert web_app > extension, "the local web app must follow the desktop extension"
    assert cli > extension, "the CLI must follow the desktop extension"
    assert "uvx --from git+https://github.com/buildproven/lifescape" in section


def test_req_free_1_first_section_needs_no_checkout_or_build() -> None:
    section = _first_section()
    assert "make mcpb" not in section
    assert "git clone" not in section
    assert "MIT" in section


def test_req_free_2_hosted_section_names_the_owner_only_instance() -> None:
    _, body = _hosted_section()
    assert "lifescape.buildproven.ai/api/mcp" in body
    assert "owner" in body.lower()
    assert "own instance" in body.lower()


def test_req_free_2_hosted_section_links_the_deploy_section() -> None:
    _, body = _hosted_section()
    titles = [title for title, _ in _sections(_readme(), "###?")]
    deploy = [title for title in titles if "deploy" in title.lower()]
    assert deploy, "README has no deploy section"
    assert f"(#{_anchor(deploy[0])})" in body


def test_req_free_2_deploy_section_names_env_example_postgres_and_github_oauth() -> None:
    deploy = [b for t, b in _sections(_readme(), "###?") if "deploy" in t.lower()]
    assert deploy, "README has no deploy section"
    body = deploy[0]
    assert ".env.example" in body
    assert "Postgres" in body
    assert "GitHub OAuth app" in body
    names = [
        line.split("=", 1)[0]
        for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#") and "=" in line
    ]
    for name in ("OWNER_GITHUB_LOGIN", "DATABASE_URL", "LIFESCAPE_OAUTH_SIGNING_KEY"):
        assert name in names, f".env.example lost {name}"
        assert f"`{name}`" in body, name


def test_req_free_3_version_fields_are_1_2_0() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert project["project"]["version"] == VERSION
    assert manifest["version"] == VERSION
    assert plugin["version"] == VERSION


def test_req_free_3_lockfile_records_the_release_version() -> None:
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    entry = re.search(r'^name = "lifescape"\nversion = "([^"]+)"', lock, re.MULTILINE)
    assert entry, "uv.lock has no lifescape package entry"
    assert entry.group(1) == VERSION


def test_req_free_3_changelog_has_a_1_2_0_entry_listing_the_release() -> None:
    text = CHANGELOG.read_text(encoding="utf-8")
    match = re.search(r"^## 1\.2\.0\b.*?(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    assert match, "CHANGELOG.md has no 1.2.0 heading"
    notes = " ".join(match.group(0).split())
    for item in (
        "/api/mcp",
        "plugin",
        "packet_id",
        "export_report",
        "reject_evidence",
        "privacy",
        "geocoder",
    ):
        assert item in notes, item
    unreleased = re.search(r"^## Unreleased\b", text, re.MULTILINE)
    if unreleased:
        assert unreleased.start() < match.start(), "Unreleased must stay above 1.2.0"
