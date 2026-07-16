from __future__ import annotations

import json
import subprocess
from pathlib import Path


def test_quality_automation_matches_project_contract() -> None:
    repository = Path(__file__).resolve().parents[1]
    package = json.loads((repository / "package.json").read_text(encoding="utf-8"))
    quality = json.loads((repository / ".qualityrc.json").read_text(encoding="utf-8"))
    workflow = (repository / ".github/workflows/quality.yml").read_text(encoding="utf-8")
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
    assert "pull_request:\n" in workflow
    assert "actions/checkout@93cb6efe18208431cddfb8368fd83d5badbf9bfd" in workflow
    assert "actions/setup-node@48b55a011bda9f5d6aeb4c2d9c7362e8dae4041e" in workflow
    assert "npm run quality:check" in workflow
    assert "npm run security:check" in workflow
    assert package["scripts"]["security:config"].endswith("bash scripts/run-gitleaks.sh")
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
