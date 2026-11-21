"""BUI-2020: one-command extension build with a pinned mcpb devDependency."""

from __future__ import annotations

import json
import os
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
    match = re.search(r"^mcpb:[^\n]*\n((?:\t[^\n]*\n)+)", makefile, re.MULTILINE)
    assert match is not None, "Makefile must define an mcpb target"
    target = match.group(1)
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


def test_req_bui_2020_7_npm_run_mcpb_builds_an_extension(tmp_path: Path) -> None:
    npm = shutil.which("npm")
    assert npm is not None, "npm must be on PATH to build the extension"
    assert (ROOT / "node_modules" / ".bin" / "mcpb").exists(), "run npm ci to install mcpb"
    out = ROOT / "dist" / "lifescape.mcpb"
    existed = out.exists()
    backup = tmp_path / "lifescape.mcpb"
    if existed:
        shutil.copy(out, backup)
    try:
        completed = subprocess.run(
            [npm, "run", "--silent", "mcpb"],
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


@pytest.mark.parametrize(
    ("dependency_state", "install_expected"),
    (
        ("missing", True),
        ("package-json-newer", True),
        ("package-lock-newer", True),
        ("current", False),
    ),
)
def test_req_bui_2020_8_make_mcpb_installs_pinned_dependency_when_missing_or_stale(
    tmp_path: Path,
    dependency_state: str,
    install_expected: bool,
) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    for name in ("Makefile", "package.json", "package-lock.json"):
        shutil.copy2(ROOT / name, checkout / name)
    skill = checkout / "skills" / "lifescape"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# Lifescape\n", encoding="utf-8")

    binary = checkout / "node_modules" / ".bin" / "mcpb"
    if dependency_state != "missing":
        binary.parent.mkdir(parents=True)
        binary.write_text("#!/bin/sh\n", encoding="utf-8")
        binary.chmod(0o755)
        baseline = 1_700_000_000
        os.utime(binary, (baseline, baseline))
        os.utime(checkout / "package.json", (baseline, baseline))
        os.utime(checkout / "package-lock.json", (baseline, baseline))
        if dependency_state == "package-json-newer":
            os.utime(checkout / "package.json", (baseline + 1, baseline + 1))
        elif dependency_state == "package-lock-newer":
            os.utime(checkout / "package-lock.json", (baseline + 1, baseline + 1))
        else:
            os.utime(binary, (baseline + 1, baseline + 1))

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "npm-calls"
    npm = bin_dir / "npm"
    npm.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$*\" >> '{calls}'\n"
        'if [ "$1" = ci ]; then\n'
        "  mkdir -p node_modules/.bin\n"
        "  touch node_modules/.bin/mcpb\n"
        "  exit 0\n"
        "fi\n"
        "if [ -x node_modules/.bin/mcpb ] || [ -f node_modules/.bin/mcpb ]; then\n"
        "  touch dist/lifescape.mcpb\n"
        "  exit 0\n"
        "fi\n"
        "exit 127\n",
        encoding="utf-8",
    )
    npm.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"

    completed = subprocess.run(
        ["make", "mcpb"],
        cwd=checkout,
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    expected_calls = ["run --silent mcpb"]
    if install_expected:
        expected_calls.insert(0, "ci --ignore-scripts --no-audit --no-fund")
    assert calls.read_text("utf-8").splitlines() == expected_calls
    assert (checkout / "dist" / "lifescape.mcpb").exists()
    assert (checkout / "dist" / "lifescape-skill.zip").exists()


def test_req_bui_2020_9_make_mcpb_removes_stale_outputs_before_install_failure(
    tmp_path: Path,
) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    for name in ("Makefile", "package.json", "package-lock.json"):
        shutil.copy2(ROOT / name, checkout / name)
    dist = checkout / "dist"
    dist.mkdir()
    stale_outputs = (dist / "lifescape.mcpb", dist / "lifescape-skill.zip")
    for output in stale_outputs:
        output.write_text("stale\n", encoding="utf-8")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    npm = bin_dir / "npm"
    npm.write_text("#!/bin/sh\nexit 42\n", encoding="utf-8")
    npm.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"

    completed = subprocess.run(
        ["make", "mcpb"],
        cwd=checkout,
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=30,
    )

    assert completed.returncode != 0
    assert not [output for output in stale_outputs if output.exists()]
