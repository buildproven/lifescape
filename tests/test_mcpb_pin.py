"""BUI-2020: one-command extension build with a pinned mcpb devDependency."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MCPB_PACKAGE = "@anthropic-ai/mcpb"
EXACT_VERSION = re.compile(r"^\d+\.\d+\.\d+$")


def _package() -> dict[str, object]:
    data: dict[str, object] = json.loads((ROOT / "package.json").read_text("utf-8"))
    return data


def _dev_dependencies() -> dict[str, str]:
    deps = _package()["devDependencies"]
    assert isinstance(deps, dict)
    return deps


def test_req_bui_2020_1_mcpb_is_a_pinned_dev_dependency() -> None:
    version = _dev_dependencies().get(MCPB_PACKAGE)
    assert version is not None, "package.json devDependencies must list @anthropic-ai/mcpb"
    assert EXACT_VERSION.match(version), f"mcpb must be pinned exactly, got {version!r}"


def test_req_bui_2020_2_lockfile_resolves_the_pinned_version() -> None:
    lock = json.loads((ROOT / "package-lock.json").read_text("utf-8"))
    pinned = _dev_dependencies()[MCPB_PACKAGE]
    root = lock["packages"][""]
    assert root["devDependencies"][MCPB_PACKAGE] == pinned
    entry = lock["packages"][f"node_modules/{MCPB_PACKAGE}"]
    assert entry["version"] == pinned
    assert entry.get("dev") is True
    assert str(entry["integrity"]).startswith("sha512-")


def test_req_bui_2020_3_mcpb_script_packs_the_extension() -> None:
    scripts = _package()["scripts"]
    assert isinstance(scripts, dict)
    script = scripts.get("mcpb")
    assert script is not None, "package.json must define an `mcpb` script"
    assert "mcpb pack" in script
    assert "dist/lifescape.mcpb" in script
    assert "npx" not in script


def test_req_bui_2020_4_make_target_uses_the_pinned_dependency() -> None:
    makefile = (ROOT / "Makefile").read_text("utf-8")
    target = makefile.split("\nmcpb:\n", 1)[1].split("\n\n", 1)[0]
    assert "npm run" in target and "mcpb" in target
    assert "npx" not in target, "make mcpb must not fetch an unpinned or inline-pinned mcpb"
    assert "@anthropic-ai/mcpb@" not in makefile


def test_req_bui_2020_5_readme_documents_the_one_command_build() -> None:
    readme = (ROOT / "README.md").read_text("utf-8")
    assert "make mcpb" in readme
    assert "npm run mcpb" in readme
    assert "package.json" in readme


def test_req_bui_2020_6_mcpbignore_keeps_local_files_out() -> None:
    patterns = (ROOT / ".mcpbignore").read_text("utf-8").splitlines()
    for required in (".venv", "node_modules", ".git", "outputs"):
        assert required in patterns


@pytest.mark.skipif(
    not (ROOT / "node_modules" / ".bin" / "mcpb").exists() or shutil.which("npm") is None,
    reason="mcpb is not installed (run npm ci)",
)
def test_req_bui_2020_7_npm_run_mcpb_builds_an_extension(tmp_path: Path) -> None:
    out = ROOT / "dist" / "lifescape.mcpb"
    existed = out.exists()
    backup = tmp_path / "lifescape.mcpb"
    if existed:
        shutil.copy(out, backup)
    try:
        completed = subprocess.run(
            ["npm", "run", "--silent", "mcpb"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        with zipfile.ZipFile(out) as bundle:
            names = bundle.namelist()
        assert "manifest.json" in names
        assert not any(n.startswith(("node_modules/", ".venv/", ".git/")) for n in names)
    finally:
        if existed:
            shutil.copy(backup, out)
        else:
            out.unlink(missing_ok=True)
