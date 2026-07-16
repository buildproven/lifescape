from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from retirement_engine.web import create_app


def test_local_app_loads_guided_workspace(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "output")) as client:
        page = client.get("/")
        bootstrap = client.get("/api/bootstrap")

    assert page.status_code == 200
    assert "Lifescape" in page.text
    assert "Shape the decision" in page.text
    assert bootstrap.status_code == 200
    assert len(bootstrap.json()["places"]) == 10
    assert bootstrap.json()["metric_count"] == 17


def test_local_app_runs_selected_towns_and_serves_reports(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "output")) as client:
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
        assert result["synthetic"] is True
        report = client.get(result["downloads"]["comparison.md"])

    assert report.status_code == 200
    assert "Synthetic benchmark warning" in report.text
    assert (tmp_path / "output/lifescape.sqlite").is_file()


def test_local_app_inspects_imported_evidence(tmp_path: Path) -> None:
    evidence = Path("data/benchmarks/evidence.csv").read_text(encoding="utf-8")
    with TestClient(create_app(tmp_path / "output")) as client:
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
    with TestClient(create_app(tmp_path / "output")) as client:
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
    assert response.json()["synthetic"] is False


def test_local_app_rejects_unknown_town_selection(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "output")) as client:
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
