from __future__ import annotations

import json
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
    assert "npm run quality:check" in workflow
    assert "npm run security:check" in workflow
    assert "lint-staged" in pre_commit
    assert "npm run quality:check" in pre_push
    assert "npm run security:check" in pre_push
