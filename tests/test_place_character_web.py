"""Web contract tests for place-character discovery qualities (BUI-1396; PRD FR4, FR5)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from lifescape.web import create_app


def test_discovery_bootstrap_marks_only_elevation_as_signed(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "output"), base_url="http://127.0.0.1") as client:
        fields = client.get("/api/bootstrap").json()["discovery"]["fields"]

    signed = {item["field"] for item in fields if item["signed"]}
    assert signed == {"elevation_ft"}
