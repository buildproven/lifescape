from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


def test_quality_automation_matches_project_contract() -> None:
    repository = Path(__file__).resolve().parents[1]
    package = json.loads((repository / "package.json").read_text(encoding="utf-8"))
    quality = json.loads((repository / ".qualityrc.json").read_text(encoding="utf-8"))
    firewall = json.loads((repository / "ops/vercel-firewall.json").read_text(encoding="utf-8"))
    workflow = (repository / ".github/workflows/quality.yml").read_text(encoding="utf-8")
    vercel = json.loads((repository / "vercel.json").read_text(encoding="utf-8"))
    pre_commit = (repository / ".husky/pre-commit").read_text(encoding="utf-8")
    pre_push = (repository / ".husky/pre-push").read_text(encoding="utf-8")

    assert package["scripts"]["quality:check"]
    assert package["scripts"]["security:check"]
    assert quality["maturity"] == "production-ready"
    assert quality["checks"]["coverage"] == {
        "enabled": True,
        "required": True,
        "threshold": 90,
    }
    assert "uv sync --locked --extra dev" in workflow
    assert "fetch-depth: 0" in workflow
    assert "pull_request:\n" in workflow
    assert "actions/checkout@93cb6efe18208431cddfb8368fd83d5badbf9bfd" in workflow
    assert "actions/setup-node@48b55a011bda9f5d6aeb4c2d9c7362e8dae4041e" in workflow
    assert "npm run quality:check" in workflow
    assert "npm run security:check" in workflow
    assert package["scripts"]["security:config"].endswith("bash scripts/run-gitleaks.sh")
    assert package["scripts"]["ops:verify:vercel"] == "bash scripts/verify-vercel-firewall.sh"
    assert package["scripts"]["quality:python"].endswith(
        "uv run --extra dev -- uv build --no-build-isolation"
    )
    assert 'requires = ["hatchling==1.31.0"]' in (repository / "pyproject.toml").read_text(
        encoding="utf-8"
    )
    gitleaks_runner = (repository / "scripts/run-gitleaks.sh").read_text(encoding="utf-8")
    firewall_verifier = (repository / "scripts/verify-vercel-firewall.sh").read_text(
        encoding="utf-8"
    )
    history_config = (repository / ".gitleaks.toml").read_text(encoding="utf-8")
    directory_config = (repository / ".gitleaks-dir.toml").read_text(encoding="utf-8")
    assert '"$binary" dir --config .gitleaks-dir.toml' in gitleaks_runner
    assert '"$binary" git --config .gitleaks.toml' in gitleaks_runner
    assert "--no-git" not in gitleaks_runner
    assert "[allowlist]" not in history_config
    assert 'path = ".gitleaks.toml"' in directory_config
    assert vercel["functions"]["api/index.py"]["maxDuration"] == 30
    assert firewall["rateLimit"] == {
        "id": "rule_rate_limit_lifescape_hosted_runs_MaiEPl",
        "name": "Rate limit Lifescape hosted runs",
        "path": "/api/run",
        "requests": 10,
        "windowSeconds": 60,
        "key": "ip",
        "status": "Enabled",
    }
    assert firewall["emergencyDeny"]["status"] == "Disabled"
    assert 'emergency_status="${VERCEL_EMERGENCY_STATUS:-Disabled}"' in firewall_verifier
    assert "lint-staged" in pre_commit
    assert "npm run quality:check" in pre_push
    assert "npm run security:check" in pre_push


def test_gitleaks_runner_rejects_poisoned_cached_binary(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[1]
    binary = tmp_path / ".cache/tools/gitleaks/8.30.1/gitleaks"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    binary.chmod(0o755)

    completed = subprocess.run(
        ["bash", str(repository / "scripts/run-gitleaks.sh")],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "FAILED" in completed.stdout + completed.stderr


def test_gitleaks_runner_detects_secret_removed_from_worktree(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[1]
    runner = repository / "scripts/run-gitleaks.sh"
    subprocess.run(
        ["bash", str(runner)],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
        errors="replace",
    )

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "quality@example.invalid"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Quality Test"],
        cwd=tmp_path,
        check=True,
    )
    shutil.copy2(repository / ".gitleaks.toml", tmp_path / ".gitleaks.toml")
    shutil.copy2(repository / ".gitleaks-dir.toml", tmp_path / ".gitleaks-dir.toml")
    secret_path = tmp_path / "dist/temporary.env"
    secret_path.parent.mkdir()
    secret_path.write_text(
        "api_key='" + "test-secret-value-123" + "'\n",
        encoding="utf-8",
    )
    subprocess.run(
        [
            "git",
            "add",
            ".gitleaks.toml",
            ".gitleaks-dir.toml",
            "dist/temporary.env",
        ],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "commit", "-q", "-m", "test: add temporary credential"],
        cwd=tmp_path,
        check=True,
    )
    secret_path.unlink()
    subprocess.run(["git", "add", "-u"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "test: remove temporary credential"],
        cwd=tmp_path,
        check=True,
    )
    shutil.copytree(
        repository / ".cache/tools/gitleaks",
        tmp_path / ".cache/tools/gitleaks",
    )

    completed = subprocess.run(
        ["bash", str(runner)],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        errors="replace",
    )

    assert completed.returncode != 0
    assert "dist/temporary.env" in completed.stdout + completed.stderr
