from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        # A git hook exports GIT_DIR and friends; without this, the commands
        # act on the repository being pushed instead of the temporary one.
        env={key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--quiet")
    shutil.copyfile(ROOT / ".gitignore", repo / ".gitignore")
    return repo


def _untracked(repo: Path) -> list[str]:
    return _git(repo, "ls-files", "--others", "--exclude-standard").splitlines()


def test_gitignore_ignores_symlinked_node_modules(repo: Path, tmp_path: Path) -> None:
    shared = tmp_path / "shared-node_modules"
    shared.mkdir()
    (shared / "package.json").write_text("{}\n", encoding="utf-8")
    (repo / "node_modules").symlink_to(shared, target_is_directory=True)

    assert _untracked(repo) == [".gitignore"]


def test_gitignore_ignores_node_modules_directory(repo: Path) -> None:
    (repo / "node_modules" / "pkg").mkdir(parents=True)
    (repo / "node_modules" / "pkg" / "index.js").write_text("\n", encoding="utf-8")

    assert _untracked(repo) == [".gitignore"]
