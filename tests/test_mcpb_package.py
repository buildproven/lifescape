"""BUI-2019: ``make mcpb`` packs the desktop extension and the skill ZIP from a working checkout.

``.mcpbignore`` keeps the local environment, installed packages, generated output, version control,
and caches out of ``dist/lifescape.mcpb``, so the package no longer needs a fresh clone.
"""

from __future__ import annotations

import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).resolve().parents[1]
IGNORE_FILE = REPOSITORY / ".mcpbignore"
MCPB = "dist/lifescape.mcpb"
SKILL_ZIP = "dist/lifescape-skill.zip"

# Files a working checkout holds that must never reach the bundle.
LOCAL_JUNK = (
    ".venv/lib/python3.12/site-packages/junk.py",
    ".venv/pyvenv.cfg",
    "node_modules/junk/index.js",
    "outputs/benchmark/report.md",
    ".git/HEAD",
    "dist/stale.txt",
    "coverage/index.html",
    ".coverage",
    ".vercel/project.json",
    "src/lifescape/__pycache__/cli.cpython-312.pyc",
    "tests/__pycache__/conftest.cpython-312.pyc",
    ".pytest_cache/v/cache/lastfailed",
    ".mypy_cache/3.12/cache.db",
    ".ruff_cache/0.6.0/cache",
    ".cache/junk",
    "lifescape.sqlite",
    ".env",
    ".env.local",
    ".DS_Store",
    ".claude/settings.local.json",
    "skills/lifescape/__pycache__/junk.cpython-312.pyc",
    "skills/lifescape/.DS_Store",
)
JUNK_PREFIXES = (
    ".venv/",
    "node_modules/",
    "outputs/",
    ".git/",
    "dist/",
    "coverage/",
    ".vercel/",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    ".cache/",
    ".claude/",
)
# What `uv run --frozen --directory ${__dirname} lifescape mcp` needs from the bundle.
RUNTIME_FILES = (
    "manifest.json",
    "pyproject.toml",
    "uv.lock",
    "README.md",
    "LICENSE",
    "src/lifescape/mcp_server.py",
    "src/lifescape/cli.py",
    "src/lifescape/data/place-catalog.csv.gz",
    "src/lifescape/data/place-catalog.manifest.json",
)


def _checkout_files() -> list[str]:
    """Tracked and new, not-ignored files: what a working checkout holds besides local junk."""
    completed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=REPOSITORY,
        capture_output=True,
        check=True,
    )
    names = [name for name in completed.stdout.decode("utf-8").split("\0") if name]
    return [name for name in names if (REPOSITORY / name).is_file()]


def _working_checkout(tmp_path: Path) -> tuple[Path, list[str]]:
    """Copy the checkout into ``tmp_path`` and plant the local files a real checkout holds."""
    checkout = tmp_path / "checkout"
    files = _checkout_files()
    for name in files:
        target = checkout / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPOSITORY / name, target)
    for name in LOCAL_JUNK:
        target = checkout / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("local only\n", encoding="utf-8")
    return checkout, files


def _make_mcpb(checkout: Path) -> subprocess.CompletedProcess[str]:
    for tool in ("make", "npx", "zip"):
        if shutil.which(tool) is None:
            pytest.fail(f"{tool} is required to build the desktop extension")
    return subprocess.run(
        ["make", "mcpb"],
        cwd=checkout,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def _ignore_patterns() -> list[str]:
    lines = IGNORE_FILE.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


def test_mcpbignore_lists_the_local_directories_and_caches() -> None:
    patterns = _ignore_patterns()

    for required in (
        ".venv",
        "node_modules",
        "outputs",
        ".git",
        "dist",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".cache",
    ):
        assert required in patterns
    # mcpb checks a directory by its bare path, so "name/" would never match one.
    assert not [pattern for pattern in patterns if pattern.endswith("/")]
    # The frozen uv runtime needs the lock file and the sources in the bundle.
    for needed in ("uv.lock", "pyproject.toml", "src", "manifest.json"):
        assert needed not in patterns


def test_make_mcpb_packs_both_packages_without_local_files(tmp_path: Path) -> None:
    checkout, files = _working_checkout(tmp_path)
    stale_skill = checkout / SKILL_ZIP
    with zipfile.ZipFile(stale_skill, "w") as archive:
        archive.writestr("lifescape/STALE.md", "from an earlier build")

    completed = _make_mcpb(checkout)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    with zipfile.ZipFile(checkout / MCPB) as archive:
        bundled = set(archive.namelist())
    assert not [name for name in bundled if name.startswith(JUNK_PREFIXES)]
    assert not [name for name in bundled if "__pycache__/" in name or name.endswith(".pyc")]
    assert not [name for name in bundled if name.endswith((".sqlite", ".mcpb", ".DS_Store"))]
    assert not [name for name in bundled if name.split("/")[-1].startswith(".env")]
    assert ".coverage" not in bundled
    assert bundled <= set(files)
    for name in RUNTIME_FILES:
        assert name in bundled
    for name in files:
        if name.startswith(("src/", "config/", "data/", "skills/")):
            assert name in bundled

    with zipfile.ZipFile(stale_skill) as archive:
        skill_entries = {name for name in archive.namelist() if not name.endswith("/")}
    expected_skill = {
        name.removeprefix("skills/") for name in files if name.startswith("skills/lifescape/")
    }
    assert "lifescape/SKILL.md" in expected_skill
    assert skill_entries == expected_skill


def test_make_mcpb_fails_when_the_manifest_is_broken(tmp_path: Path) -> None:
    checkout, _ = _working_checkout(tmp_path)
    (checkout / "manifest.json").write_text('{"name": "lifescape"}\n', encoding="utf-8")

    completed = _make_mcpb(checkout)

    assert completed.returncode != 0
    assert not (checkout / MCPB).exists()
    assert not (checkout / SKILL_ZIP).exists()
