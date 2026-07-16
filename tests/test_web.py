from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from retirement_engine.web import create_app


def test_local_app_loads_guided_workspace(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "output"), base_url="http://127.0.0.1") as client:
        page = client.get("/")
        bootstrap = client.get("/api/bootstrap")

    assert page.status_code == 200
    assert "Lifescape" in page.text
    assert "Shape the decision" in page.text
    assert bootstrap.status_code == 200
    assert len(bootstrap.json()["places"]) == 10
    assert bootstrap.json()["metric_count"] == 17


def test_local_app_runs_selected_towns_and_serves_reports(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "output"), base_url="http://127.0.0.1") as client:
        places = client.get("/api/bootstrap").json()["places"]
        selected = [place["place_id"] for place in places[:4]]
        response = client.post(
            "/api/run",
            json={
                "selected_place_ids": selected,
                "purchase_budget_max": 700_000,
                "future_self_age": 75,
                "household": "couple",
            },
        )

        assert response.status_code == 200, response.text
        result = response.json()
        assert len(result["rankings"]) + len(result["blocked"]) == 4
        assert result["evidence_kind"] == "synthetic"
        assert result["has_synthetic"] is True
        report = client.get(result["downloads"]["comparison.md"])
        database = client.get(result["downloads"]["lifescape.sqlite"])

    assert report.status_code == 200
    assert "Synthetic benchmark warning" in report.text
    assert database.status_code == 200
    database_path = next((tmp_path / "output/runs").glob("*/lifescape.sqlite"))
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM research_runs").fetchone() == (1,)


def test_local_app_inspects_imported_evidence(tmp_path: Path) -> None:
    evidence = Path("data/benchmarks/evidence.csv").read_text(encoding="utf-8")
    with TestClient(create_app(tmp_path / "output"), base_url="http://127.0.0.1") as client:
        response = client.post("/api/evidence/inspect", json={"csv_text": evidence})
        invalid = client.post("/api/evidence/inspect", json={"csv_text": "place_id\nx\n"})

    assert response.status_code == 200
    assert response.json()["places"][0]["total_metrics"] == 17
    assert response.json()["evidence_kind"] == "synthetic"
    assert invalid.status_code == 422
    assert "missing columns" in invalid.json()["detail"]


def test_local_app_runs_imported_real_evidence_without_synthetic_label(tmp_path: Path) -> None:
    evidence = Path("data/benchmarks/evidence.csv").read_text(encoding="utf-8")
    real_evidence = evidence.replace(",true,", ",false,")
    with TestClient(create_app(tmp_path / "output"), base_url="http://127.0.0.1") as client:
        inspection = client.post("/api/evidence/inspect", json={"csv_text": real_evidence}).json()
        selected = [place["place_id"] for place in inspection["places"][:2]]
        response = client.post(
            "/api/run",
            json={
                "selected_place_ids": selected,
                "purchase_budget_max": 700_000,
                "future_self_age": 75,
                "household": "couple",
                "evidence_csv": real_evidence,
            },
        )

    assert inspection["evidence_kind"] == "real"
    assert response.status_code == 200, response.text
    assert response.json()["evidence_kind"] == "real"
    assert response.json()["has_synthetic"] is False


def test_local_app_preserves_mixed_evidence_warning(tmp_path: Path) -> None:
    evidence = Path("data/benchmarks/evidence.csv").read_text(encoding="utf-8")
    mixed_evidence = evidence.replace(",true,", ",false,", 1)
    with TestClient(create_app(tmp_path / "output"), base_url="http://127.0.0.1") as client:
        inspection = client.post("/api/evidence/inspect", json={"csv_text": mixed_evidence}).json()
        selected = [place["place_id"] for place in inspection["places"][:2]]
        response = client.post(
            "/api/run",
            json={
                "selected_place_ids": selected,
                "purchase_budget_max": 700_000,
                "future_self_age": 75,
                "household": "couple",
                "evidence_csv": mixed_evidence,
            },
        )

    assert inspection["evidence_kind"] == "mixed"
    assert response.status_code == 200, response.text
    assert response.json()["evidence_kind"] == "mixed"
    assert response.json()["has_synthetic"] is True


def test_local_app_rejects_unknown_town_selection(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "output"), base_url="http://127.0.0.1") as client:
        response = client.post(
            "/api/run",
            json={
                "selected_place_ids": ["unknown", "also_unknown"],
                "purchase_budget_max": 700_000,
                "future_self_age": 75,
                "household": "couple",
            },
        )

    assert response.status_code == 422
    assert "unknown selected places" in response.json()["detail"]
    assert list((tmp_path / "output/runs").iterdir()) == []


def test_local_app_rejects_hostile_host_and_origin(tmp_path: Path) -> None:
    app = create_app(tmp_path / "output")
    with TestClient(app, base_url="http://127.0.0.1") as client:
        hostile_host = client.get("/api/bootstrap", headers={"host": "attacker.example"})
        hostile_origin = client.post(
            "/api/evidence/inspect",
            headers={"origin": "http://attacker.example"},
            json={"csv_text": "not reached"},
        )

    assert hostile_host.status_code == 400
    assert hostile_origin.status_code == 403


def test_local_app_bounds_inspection_before_parsing(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "output"), base_url="http://127.0.0.1") as client:
        model_limit = client.post("/api/evidence/inspect", json={"csv_text": "x" * 5_000_001})
        transport_limit = client.post(
            "/api/evidence/inspect",
            content=b'{"csv_text":"' + b"x" * 5_100_001 + b'"}',
            headers={"content-type": "application/json"},
        )

    assert model_limit.status_code == 422
    assert "5 MB" in model_limit.text
    assert transport_limit.status_code == 413


def test_local_app_does_not_publish_partial_failed_run(tmp_path: Path) -> None:
    output = tmp_path / "output"

    def fail_after_partial_write(**kwargs: object) -> None:
        output_dir = kwargs["output_dir"]
        assert isinstance(output_dir, Path)
        (output_dir / "lifescape.sqlite").write_bytes(b"partial")
        (output_dir / "comparison.md").write_text("partial", encoding="utf-8")
        raise ValueError("report generation failed")

    with (
        patch("retirement_engine.web.execute_run", side_effect=fail_after_partial_write),
        TestClient(create_app(output), base_url="http://127.0.0.1") as client,
    ):
        places = client.get("/api/bootstrap").json()["places"]
        response = client.post(
            "/api/run",
            json={
                "selected_place_ids": [place["place_id"] for place in places[:2]],
                "purchase_budget_max": 700_000,
                "future_self_age": 75,
                "household": "couple",
            },
        )

    assert response.status_code == 422
    assert response.json()["detail"] == "report generation failed"
    assert list((output / "runs").iterdir()) == []
