from __future__ import annotations

import runpy
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
    assert "Your evidence and outputs stay on this computer." in page.text
    assert "CSV uploads are disabled." not in page.text
    assert len(bootstrap.json()["places"]) == 10
    assert bootstrap.json()["metric_count"] == 17


def test_hosted_demo_is_synthetic_and_stateless(tmp_path: Path) -> None:
    output = tmp_path / "output"
    with TestClient(
        create_app(output, hosted_demo=True, hosted_runs_enabled=True),
        base_url="https://lifescape.buildproven.ai",
    ) as client:
        page = client.get("/")
        bootstrap = client.get("/api/bootstrap")
        places = bootstrap.json()["places"]
        imported = client.post(
            "/api/evidence/inspect",
            content=b"private evidence",
            headers={"content-type": "text/csv"},
        )
        response = client.post(
            "/api/run",
            headers={"origin": "https://lifescape.buildproven.ai"},
            json={
                "selected_place_ids": [place["place_id"] for place in places[:4]],
                "purchase_budget_max": 700_000,
                "future_self_age": 75,
                "household": "couple",
            },
        )
        download = client.get("/api/downloads/000000000000/comparison.md")

    assert bootstrap.status_code == 200
    assert "CSV uploads are disabled." in page.text
    assert "stay on this computer" not in page.text
    assert bootstrap.json()["mode"] == "hosted-demo"
    assert bootstrap.json()["allow_imports"] is False
    assert bootstrap.json()["persistent_outputs"] is False
    assert imported.status_code == 403
    assert response.status_code == 200
    assert response.json()["downloads"] == {}
    assert download.status_code == 404
    assert list((output / "runs").iterdir()) == []


def test_hosted_demo_accepts_https_preview_origin(tmp_path: Path) -> None:
    preview_host = "lifescape-example-buildproven.vercel.app"
    with TestClient(
        create_app(
            tmp_path / "output",
            hosted_demo=True,
            hosted_runs_enabled=True,
        ),
        base_url=f"https://{preview_host}",
    ) as client:
        places = client.get("/api/bootstrap").json()["places"]
        response = client.post(
            "/api/run",
            headers={
                "origin": f"https://{preview_host}",
                "x-forwarded-proto": "https",
            },
            json={
                "selected_place_ids": [place["place_id"] for place in places[:2]],
                "purchase_budget_max": 700_000,
                "future_self_age": 75,
                "household": "couple",
            },
        )

    assert response.status_code == 200


def test_vercel_entrypoint_exposes_hosted_demo() -> None:
    vercel_app = runpy.run_path("api/index.py")["app"]
    with TestClient(vercel_app, base_url="https://lifescape.buildproven.ai") as client:
        response = client.get("/api/bootstrap")

    assert response.status_code == 200
    assert response.json()["mode"] == "hosted-demo"


def test_hosted_demo_fails_closed_when_runs_are_disabled(tmp_path: Path) -> None:
    output = tmp_path / "output"
    with (
        patch("retirement_engine.web.execute_run") as execute,
        TestClient(
            create_app(output, hosted_demo=True),
            base_url="https://lifescape.buildproven.ai",
        ) as client,
    ):
        places = client.get("/api/bootstrap").json()["places"]
        response = client.post(
            "/api/run",
            headers={"origin": "https://lifescape.buildproven.ai"},
            json={
                "selected_place_ids": [place["place_id"] for place in places[:2]],
                "purchase_budget_max": 700_000,
                "future_self_age": 75,
                "household": "couple",
            },
        )

    assert response.status_code == 503
    assert response.headers["retry-after"] == "60"
    assert execute.call_count == 0
    assert not output.exists()


def test_hosted_demo_requires_origin_and_rate_limits_runs(tmp_path: Path) -> None:
    output = tmp_path / "output"
    with TestClient(
        create_app(
            output,
            hosted_demo=True,
            hosted_runs_enabled=True,
            hosted_run_limit=1,
        ),
        base_url="https://lifescape.buildproven.ai",
    ) as client:
        places = client.get("/api/bootstrap").json()["places"]
        payload = {
            "selected_place_ids": [place["place_id"] for place in places[:2]],
            "purchase_budget_max": 700_000,
            "future_self_age": 75,
            "household": "couple",
        }
        missing_origin = client.post("/api/run", json=payload)
        first = client.post(
            "/api/run",
            headers={
                "origin": "https://lifescape.buildproven.ai",
                "x-forwarded-for": "203.0.113.5",
            },
            json=payload,
        )
        limited = client.post(
            "/api/run",
            headers={
                "origin": "https://lifescape.buildproven.ai",
                "x-forwarded-for": "203.0.113.5",
            },
            json=payload,
        )

    assert missing_origin.status_code == 403
    assert first.status_code == 200
    assert limited.status_code == 429
    assert int(limited.headers["retry-after"]) >= 1
    assert list((output / "runs").iterdir()) == []


def test_local_html_preserves_local_disclosure(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "output"), base_url="http://127.0.0.1") as client:
        response = client.get("/")

    assert "Your evidence and outputs stay on this computer." in response.text
    assert "CSV uploads are disabled." not in response.text


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
        response = client.post(
            "/api/evidence/inspect",
            content=evidence.encode(),
            headers={"content-type": "text/csv"},
        )
        invalid = client.post(
            "/api/evidence/inspect",
            content=b"place_id\nx\n",
            headers={"content-type": "text/csv"},
        )

    assert response.status_code == 200
    assert response.json()["places"][0]["total_metrics"] == 17
    assert response.json()["evidence_kind"] == "synthetic"
    assert invalid.status_code == 422
    assert "missing columns" in invalid.json()["detail"]


def test_local_app_returns_visible_error_for_malformed_quoted_record(tmp_path: Path) -> None:
    evidence = Path("data/benchmarks/evidence.csv").read_text(encoding="utf-8")
    header, valid_row, *_ = evidence.splitlines()
    malformed = f'{header}\n{valid_row}\n"unterminated'.encode()
    with TestClient(create_app(tmp_path / "output"), base_url="http://127.0.0.1") as client:
        response = client.post(
            "/api/evidence/inspect",
            content=malformed,
            headers={"content-type": "text/csv"},
        )

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/json")
    assert "malformed" in response.json()["detail"]


def test_local_app_rejects_duplicate_evidence_headers_before_execution(
    tmp_path: Path,
) -> None:
    evidence = Path("data/benchmarks/evidence.csv").read_text(encoding="utf-8")
    duplicate_identity = evidence.replace("place_id,", "place_id,place_id,", 1).encode()
    duplicate_metric = evidence.replace(
        "median_sale_price,",
        "median_sale_price,median_sale_price,",
        1,
    ).encode()
    output = tmp_path / "output"
    with (
        patch("retirement_engine.web.execute_run") as execute,
        TestClient(create_app(output), base_url="http://127.0.0.1") as client,
    ):
        identity_response = client.post(
            "/api/evidence/inspect",
            content=duplicate_identity,
            headers={"content-type": "text/csv"},
        )
        metric_response = client.post(
            "/api/evidence/inspect",
            content=duplicate_metric,
            headers={"content-type": "text/csv"},
        )

    assert identity_response.status_code == 422
    assert "duplicate columns" in identity_response.json()["detail"]
    assert metric_response.status_code == 422
    assert "duplicate columns" in metric_response.json()["detail"]
    assert execute.call_count == 0
    assert not output.exists()


def test_local_app_rejects_stray_quotes_but_accepts_escaped_quotes(tmp_path: Path) -> None:
    evidence = Path("data/benchmarks/evidence.csv").read_text(encoding="utf-8")
    header, first_row, *_ = evidence.splitlines()
    malformed_header = evidence.replace("place_id,", 'place_id",', 1).encode()
    malformed_row = (
        f"{header}\n{first_row.replace('libertyville_il', 'libertyville_il"oops')}".encode()
    )
    escaped_quote = evidence.replace(",Libertyville,", ',"Libertyville ""North""",', 1).encode()

    with TestClient(create_app(tmp_path / "output"), base_url="http://127.0.0.1") as client:
        header_response = client.post(
            "/api/evidence/inspect",
            content=malformed_header,
            headers={"content-type": "text/csv"},
        )
        row_response = client.post(
            "/api/evidence/inspect",
            content=malformed_row,
            headers={"content-type": "text/csv"},
        )
        valid_response = client.post(
            "/api/evidence/inspect",
            content=escaped_quote,
            headers={"content-type": "text/csv"},
        )

    assert header_response.status_code == 422
    assert "quote inside an unquoted field" in header_response.json()["detail"]
    assert row_response.status_code == 422
    assert "quote inside an unquoted field" in row_response.json()["detail"]
    assert valid_response.status_code == 200
    libertyville = next(
        place for place in valid_response.json()["places"] if place["place_id"] == "libertyville_il"
    )
    assert libertyville["name"] == 'Libertyville "North"'


def test_local_app_runs_imported_real_evidence_without_synthetic_label(tmp_path: Path) -> None:
    evidence = Path("data/benchmarks/evidence.csv").read_text(encoding="utf-8")
    real_evidence = evidence.replace(",true,", ",false,")
    with TestClient(create_app(tmp_path / "output"), base_url="http://127.0.0.1") as client:
        inspection = client.post(
            "/api/evidence/inspect",
            content=real_evidence.encode(),
            headers={"content-type": "text/csv"},
        ).json()
        selected = [place["place_id"] for place in inspection["places"][:2]]
        response = client.post(
            "/api/run",
            json={
                "selected_place_ids": selected,
                "purchase_budget_max": 700_000,
                "future_self_age": 75,
                "household": "couple",
                "evidence_token": inspection["evidence_token"],
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
        inspection = client.post(
            "/api/evidence/inspect",
            content=mixed_evidence.encode(),
            headers={"content-type": "text/csv"},
        ).json()
        selected = [place["place_id"] for place in inspection["places"][:2]]
        response = client.post(
            "/api/run",
            json={
                "selected_place_ids": selected,
                "purchase_budget_max": 700_000,
                "future_self_age": 75,
                "household": "couple",
                "evidence_token": inspection["evidence_token"],
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
            content=b"not reached",
        )

    assert hostile_host.status_code == 400
    assert hostile_origin.status_code == 403


def test_local_app_bounds_raw_inspection_before_parsing(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "output"), base_url="http://127.0.0.1") as client:
        escaping_heavy_in_limit = client.post(
            "/api/evidence/inspect",
            content=b'"' * 5_000_000,
            headers={"content-type": "text/csv"},
        )
        over_limit = client.post(
            "/api/evidence/inspect",
            content=b"x" * 5_000_001,
            headers={"content-type": "text/csv"},
        )

    assert escaping_heavy_in_limit.status_code == 422
    assert over_limit.status_code == 413


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


def test_local_app_shapes_response_before_publishing_run(tmp_path: Path) -> None:
    output = tmp_path / "output"
    with (
        patch("retirement_engine.web._response", side_effect=ValueError("response mismatch")),
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
    assert response.json()["detail"] == "response mismatch"
    assert list((output / "runs").iterdir()) == []
