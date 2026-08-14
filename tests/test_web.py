from __future__ import annotations

import runpy
import sqlite3
from datetime import date
from html.parser import HTMLParser
from inspect import signature
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from lifescape.models import Confidence, ObservationRecord, PlaceRecord, SourceRecord, SourceTier
from lifescape.pipeline import execute_run
from lifescape.research import DiscoveryLead, SearchBrief
from lifescape.research_sources import EvidenceFetchResult
from lifescape.research_workspace import ResearchWorkspace
from lifescape.web import HostedRunGuard, _read_evidence, create_app


class FakeDiscoveryProvider:
    def discover(self, brief: SearchBrief) -> tuple[DiscoveryLead, ...]:
        assert brief.exemplar_towns == ("Traverse City, MI",)
        return (
            DiscoveryLead(
                place=PlaceRecord(place_id="asheville_nc", name="Asheville", state="NC"),
                rationale="A discovery lead, not verified evidence.",
                caveats=("Verify all critical evidence.",),
            ),
        )


class EmptyDiscoveryProvider:
    def discover(self, brief: SearchBrief) -> tuple[DiscoveryLead, ...]:
        del brief
        return ()


class MultiDiscoveryProvider:
    def discover(self, brief: SearchBrief) -> tuple[DiscoveryLead, ...]:
        del brief
        return (
            DiscoveryLead(
                place=PlaceRecord(place_id="asheville_nc", name="Asheville", state="NC"),
                rationale="First lead.",
            ),
            DiscoveryLead(
                place=PlaceRecord(place_id="bend_or", name="Bend", state="OR"),
                rationale="Second lead.",
            ),
        )


class FakeResearchEvidenceProvider:
    def fetch(self, packet):
        return EvidenceFetchResult(
            observations=tuple(
                ObservationRecord(
                    place=lead.place,
                    metric_id="distress_index",
                    raw_value=2,
                    observed_period="2021-2025",
                    observed_at=date(2025, 12, 31),
                    source=SourceRecord(
                        url="https://api.census.gov/data/2025/acs/acs5/profile",
                        title="American Community Survey 2025 5-Year Estimates",
                        publisher="U.S. Census Bureau",
                        tier=SourceTier.A,
                        retrieved_at=date(2026, 1, 1),
                        geography="town",
                        confidence=Confidence.HIGH,
                    ),
                )
                for lead in packet.leads
            )
        )


class CompleteResearchEvidenceProvider:
    def fetch(self, packet):
        values = {
            "median_sale_price": 500_000,
            "er_drive_minutes": 10,
            "broadband_mbps_down": 250,
            "annual_snowfall": 10,
            "flood_risk_score": 2,
            "distress_index": 2,
            "one_level_inventory_count": 25,
        }
        return EvidenceFetchResult(
            observations=tuple(
                ObservationRecord(
                    place=lead.place,
                    metric_id=metric_id,
                    raw_value=value,
                    observed_period="2021-2025",
                    observed_at=date(2025, 12, 31),
                    source=SourceRecord(
                        url="https://api.census.gov/data/2025/acs/acs5/profile",
                        title="Official pilot fixture",
                        publisher="U.S. Census Bureau",
                        tier=SourceTier.A,
                        retrieved_at=date(2026, 1, 1),
                        geography="town",
                        confidence=Confidence.HIGH,
                    ),
                )
                for lead in packet.leads
                for metric_id, value in values.items()
            )
        )


def test_hosted_landing_page_explains_the_product_and_links_to_demo(tmp_path: Path) -> None:
    with TestClient(
        create_app(tmp_path / "output", hosted_demo=True),
        base_url="https://lifescape.buildproven.ai",
    ) as client:
        page = client.get("/")

    assert page.status_code == 200
    assert "Decide where retirement still works." in page.text
    assert "hard gates, ranked preferences, source quality, and sensitivity" in page.text
    assert 'href="/demo"' in page.text
    assert "The public demo uses invented evidence." in page.text
    assert "Use the web demo to learn it. Run locally for your real work." in page.text


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
    assert "Fetch history" in (Path(__file__).parents[1] / "src/lifescape/static/app.js").read_text(
        encoding="utf-8"
    )


def test_local_app_creates_discovery_packet_without_scoring(tmp_path: Path) -> None:
    with TestClient(
        create_app(tmp_path / "output", discovery_provider=FakeDiscoveryProvider()),
        base_url="http://127.0.0.1",
    ) as client:
        response = client.post(
            "/api/research/discover",
            json={
                "preferences": (
                    "Walkable four-season retirement town with nature and a lively core."
                ),
                "exemplar_towns": ["Traverse City, MI"],
                "hard_constraints": ["Budget below $700,000"],
                "exclusions": [],
            },
        )
        payload = response.json()
        fetched = client.get(f"/api/research/packets/{payload['packet_id']}")

    assert response.status_code == 200
    assert payload["state"] == "DISCOVERY"
    assert payload["leads"][0]["place_id"] == "asheville_nc"
    assert len(payload["leads"][0]["unresolved_critical_metrics"]) == 7
    assert "cannot affect gates or ranking" in payload["disclosure"]
    assert fetched.json() == payload


def test_discovery_refuses_a_provider_with_no_leads(tmp_path: Path) -> None:
    with TestClient(
        create_app(tmp_path / "output", discovery_provider=EmptyDiscoveryProvider()),
        base_url="http://127.0.0.1",
    ) as client:
        response = client.post(
            "/api/research/discover",
            json={
                "preferences": "A walkable retirement town.",
                "exemplar_towns": ["Traverse City, MI"],
            },
        )

    assert response.status_code == 422
    assert "at least one discovery lead" in response.json()["detail"]


def test_discovery_accepts_preferences_without_exemplar_town(tmp_path: Path) -> None:
    with TestClient(
        create_app(tmp_path / "output", discovery_provider=MultiDiscoveryProvider()),
        base_url="http://127.0.0.1",
    ) as client:
        response = client.post(
            "/api/research/discover",
            json={"preferences": "A walkable retirement town with outdoor access."},
        )

    assert response.status_code == 200
    assert response.json()["leads"]


def test_selected_leads_can_fetch_and_review_adapter_evidence(tmp_path: Path) -> None:
    with TestClient(
        create_app(
            tmp_path / "output",
            discovery_provider=MultiDiscoveryProvider(),
            research_evidence_provider=FakeResearchEvidenceProvider(),
        ),
        base_url="http://127.0.0.1",
    ) as client:
        packet = client.post(
            "/api/research/discover",
            json={"preferences": "A walkable retirement town with outdoor access."},
        ).json()
        selected = client.post(
            "/api/research/select",
            json={
                "packet_id": packet["packet_id"],
                "place_ids": ["asheville_nc", "bend_or"],
            },
        ).json()
        fetched = client.post(
            "/api/research/fetch",
            json={
                "packet_id": selected["packet_id"],
                "place_ids": ["asheville_nc", "bend_or"],
            },
        )
        evidence = fetched.json()["evidence"][0]
        approved = client.post(
            "/api/research/approve-fetched",
            json={
                "packet_id": selected["packet_id"],
                "place_id": evidence["place"]["place_id"],
                "metric_id": evidence["metric_id"],
                "reviewer": "Brett Stark",
            },
        )

    assert fetched.status_code == 200
    assert fetched.json()["evidence_status"]["asheville_nc"]["distress_index"] == "awaiting_review"
    assert approved.status_code == 200
    assert approved.json()["reviews"][0]["decision"] == "APPROVED"
    assert approved.json()["evidence_status"]["asheville_nc"]["distress_index"] == "approved"


def test_local_research_packet_resumes_after_restart_and_retains_fetch_history(
    tmp_path: Path,
) -> None:
    output = tmp_path / "output"
    with TestClient(
        create_app(
            output,
            discovery_provider=MultiDiscoveryProvider(),
            research_evidence_provider=FakeResearchEvidenceProvider(),
        ),
        base_url="http://127.0.0.1",
    ) as client:
        packet = client.post(
            "/api/research/discover",
            json={"preferences": "A walkable retirement town with a lively center and nature."},
        ).json()
        selected = client.post(
            "/api/research/select",
            json={"packet_id": packet["packet_id"], "place_ids": ["asheville_nc", "bend_or"]},
        ).json()
        fetched = client.post(
            "/api/research/fetch",
            json={"packet_id": selected["packet_id"], "place_ids": ["asheville_nc", "bend_or"]},
        ).json()
        observation = fetched["evidence"][0]
        client.post(
            "/api/research/approve-fetched",
            json={
                "packet_id": selected["packet_id"],
                "place_id": observation["place"]["place_id"],
                "metric_id": observation["metric_id"],
                "reviewer": "Brett Stark",
            },
        )
        refreshed = client.post(
            "/api/research/fetch",
            json={"packet_id": selected["packet_id"], "place_ids": ["asheville_nc", "bend_or"]},
        )

    with TestClient(create_app(output), base_url="http://127.0.0.1") as resumed_client:
        resumed = resumed_client.get(f"/api/research/packets/{selected['packet_id']}")

    assert refreshed.status_code == 200
    assert resumed.status_code == 200
    assert resumed.json()["fetch_history_count"] == 2
    assert resumed.json()["evidence_status"]["asheville_nc"]["distress_index"] == "approved"


def test_research_selection_rolls_back_when_local_workspace_cannot_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with TestClient(
        create_app(tmp_path / "output", discovery_provider=MultiDiscoveryProvider()),
        base_url="http://127.0.0.1",
    ) as client:
        packet = client.post(
            "/api/research/discover",
            json={"preferences": "A walkable retirement town with a lively center and nature."},
        ).json()
        monkeypatch.setattr(
            ResearchWorkspace, "save", lambda *_: (_ for _ in ()).throw(OSError("full"))
        )
        selected = client.post(
            "/api/research/select",
            json={"packet_id": packet["packet_id"], "place_ids": ["asheville_nc", "bend_or"]},
        )

        original = client.get(f"/api/research/packets/{packet['packet_id']}")

    assert selected.status_code == 422
    assert "cannot save local research workspace" in selected.json()["detail"]
    assert original.status_code == 200


def test_research_fetch_rolls_back_when_local_workspace_cannot_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with TestClient(
        create_app(
            tmp_path / "output",
            discovery_provider=MultiDiscoveryProvider(),
            research_evidence_provider=FakeResearchEvidenceProvider(),
        ),
        base_url="http://127.0.0.1",
    ) as client:
        packet = client.post(
            "/api/research/discover",
            json={"preferences": "A walkable retirement town with a lively center and nature."},
        ).json()
        selected = client.post(
            "/api/research/select",
            json={"packet_id": packet["packet_id"], "place_ids": ["asheville_nc", "bend_or"]},
        ).json()
        monkeypatch.setattr(
            ResearchWorkspace, "save", lambda *_: (_ for _ in ()).throw(OSError("full"))
        )
        fetched = client.post(
            "/api/research/fetch",
            json={"packet_id": selected["packet_id"], "place_ids": ["asheville_nc", "bend_or"]},
        )
        original = client.get(f"/api/research/packets/{selected['packet_id']}")

    assert fetched.status_code == 422
    assert "cannot save local research workspace" in fetched.json()["detail"]
    assert original.status_code == 200
    assert original.json()["fetch_history_count"] == 0
    assert original.json()["evidence"] == []


def test_incomplete_adapter_packet_blocks_execute_run(tmp_path: Path) -> None:
    with (
        TestClient(
            create_app(
                tmp_path / "output",
                discovery_provider=MultiDiscoveryProvider(),
                research_evidence_provider=FakeResearchEvidenceProvider(),
            ),
            base_url="http://127.0.0.1",
        ) as client,
        patch("lifescape.web.execute_run") as execute,
    ):
        packet = client.post(
            "/api/research/discover",
            json={"preferences": "A walkable retirement town with outdoor access."},
        ).json()
        selected = client.post(
            "/api/research/select",
            json={"packet_id": packet["packet_id"], "place_ids": ["asheville_nc", "bend_or"]},
        ).json()
        fetched = client.post(
            "/api/research/fetch",
            json={"packet_id": selected["packet_id"], "place_ids": ["asheville_nc", "bend_or"]},
        ).json()
        observation = fetched["evidence"][0]
        approved = client.post(
            "/api/research/approve-fetched",
            json={
                "packet_id": selected["packet_id"],
                "place_id": observation["place"]["place_id"],
                "metric_id": observation["metric_id"],
                "reviewer": "Brett Stark",
            },
        )
        run = client.post(
            "/api/run",
            json={
                "selected_place_ids": ["asheville_nc", "bend_or"],
                "purchase_budget_max": 700_000,
                "future_self_age": 75,
                "household": "couple",
                "research_packet_id": selected["packet_id"],
            },
        )

    assert approved.status_code == 200
    assert run.status_code == 422
    assert "critical evidence remains unapproved" in run.json()["detail"]
    execute.assert_not_called()


def test_complete_approved_adapter_packet_reaches_execute_run(tmp_path: Path) -> None:
    with (
        TestClient(
            create_app(
                tmp_path / "output",
                discovery_provider=MultiDiscoveryProvider(),
                research_evidence_provider=CompleteResearchEvidenceProvider(),
            ),
            base_url="http://127.0.0.1",
        ) as client,
        patch(
            "lifescape.web.execute_run", side_effect=ValueError("execute boundary reached")
        ) as execute,
    ):
        packet = client.post(
            "/api/research/discover",
            json={"preferences": "A walkable retirement town with outdoor access."},
        ).json()
        selected = client.post(
            "/api/research/select",
            json={"packet_id": packet["packet_id"], "place_ids": ["asheville_nc", "bend_or"]},
        ).json()
        fetched = client.post(
            "/api/research/fetch",
            json={"packet_id": selected["packet_id"], "place_ids": ["asheville_nc", "bend_or"]},
        ).json()
        for observation in fetched["evidence"]:
            approved = client.post(
                "/api/research/approve-fetched",
                json={
                    "packet_id": selected["packet_id"],
                    "place_id": observation["place"]["place_id"],
                    "metric_id": observation["metric_id"],
                    "reviewer": "Brett Stark",
                },
            )
            assert approved.status_code == 200
        run = client.post(
            "/api/run",
            json={
                "selected_place_ids": ["asheville_nc", "bend_or"],
                "purchase_budget_max": 700_000,
                "future_self_age": 75,
                "household": "couple",
                "research_packet_id": selected["packet_id"],
            },
        )

    assert run.status_code == 422
    assert "execute boundary reached" in run.json()["detail"]
    execute.assert_called_once()


def test_promotion_requires_existing_packet_and_never_runs_scoring(tmp_path: Path) -> None:
    with TestClient(
        create_app(tmp_path / "output", discovery_provider=FakeDiscoveryProvider()),
        base_url="http://127.0.0.1",
    ) as client:
        missing = client.post(
            "/api/research/promote",
            json={
                "packet_id": "missing",
                "reviewer": "Brett Stark",
                "place": {"place_id": "asheville_nc", "name": "Asheville", "state": "NC"},
                "metric_id": "annual_snowfall",
                "raw_value": 12,
                "observed_period": "2025",
                "observed_at": "2025-12-31",
                "source": {
                    "url": "https://www.ncei.noaa.gov/example",
                    "title": "Station observation",
                    "publisher": "NOAA",
                    "tier": "A",
                    "retrieved_at": "2026-01-01",
                    "geography": "town",
                    "confidence": "high",
                },
            },
        )

    assert missing.status_code == 404
    assert "research packet is not available" in missing.json()["detail"]


def test_manual_finalist_town_evidence_is_reviewed_saved_and_resumable(tmp_path: Path) -> None:
    output = tmp_path / "output"
    with TestClient(
        create_app(output, discovery_provider=FakeDiscoveryProvider()),
        base_url="http://127.0.0.1",
    ) as client:
        packet = client.post(
            "/api/research/discover",
            json={
                "preferences": "A walkable retirement town with outdoor access.",
                "exemplar_towns": ["Traverse City, MI"],
            },
        ).json()
        approved = client.post(
            "/api/research/promote",
            json={
                "packet_id": packet["packet_id"],
                "reviewer": "Brett Stark",
                "place": {"place_id": "asheville_nc", "name": "Asheville", "state": "NC"},
                "metric_id": "median_sale_price",
                "raw_value": 500000,
                "observed_period": "July 2026 monthly median",
                "observed_at": "2026-08-01",
                "source": {
                    "url": "https://example.gov/asheville-sales",
                    "title": "Asheville monthly sale prices",
                    "publisher": "Example public records office",
                    "tier": "A",
                    "retrieved_at": "2026-08-02",
                    "geography": "town",
                    "confidence": "high",
                    "synthetic": False,
                },
            },
        )

    assert approved.status_code == 200
    assert approved.json()["evidence_status"]["asheville_nc"]["median_sale_price"] == "approved"
    assert approved.json()["reviews"][0]["reviewer"] == "Brett Stark"

    with TestClient(create_app(output), base_url="http://127.0.0.1") as client:
        resumed = client.get(f"/api/research/packets/{packet['packet_id']}")

    assert resumed.status_code == 200
    assert resumed.json()["evidence_status"]["asheville_nc"]["median_sale_price"] == "approved"


def test_research_packet_endpoints_report_missing_session_packet(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "output"), base_url="http://127.0.0.1") as client:
        inspected = client.get("/api/research/packets/missing")
        rejected = client.post(
            "/api/research/reject",
            json={
                "packet_id": "missing",
                "reviewer": "Brett Stark",
                "place": {"place_id": "asheville_nc", "name": "Asheville", "state": "NC"},
                "metric_id": "annual_snowfall",
                "reason": "The source geography is not town-level.",
            },
        )
        exported = client.get("/api/research/packets/missing/evidence.csv")

    for response in (inspected, rejected, exported):
        assert response.status_code == 404
        assert "research packet is not available" in response.json()["detail"]


def test_research_export_refuses_incomplete_packet_and_rejection_is_visible(tmp_path: Path) -> None:
    with TestClient(
        create_app(tmp_path / "output", discovery_provider=FakeDiscoveryProvider()),
        base_url="http://127.0.0.1",
    ) as client:
        packet = client.post(
            "/api/research/discover",
            json={
                "preferences": (
                    "Walkable four-season retirement town with nature and a lively core."
                ),
                "exemplar_towns": ["Traverse City, MI"],
            },
        ).json()
        rejected = client.post(
            "/api/research/reject",
            json={
                "packet_id": packet["packet_id"],
                "reviewer": "Brett Stark",
                "place": {"place_id": "asheville_nc", "name": "Asheville", "state": "NC"},
                "metric_id": "annual_snowfall",
                "reason": "Station record cannot represent the entire town.",
            },
        )
        exported = client.get(f"/api/research/packets/{packet['packet_id']}/evidence.csv")
        with patch("lifescape.web.execute_run") as execute:
            run = client.post(
                "/api/run",
                json={
                    "selected_place_ids": ["asheville_nc"],
                    "purchase_budget_max": 700_000,
                    "future_self_age": 75,
                    "household": "couple",
                    "research_packet_id": packet["packet_id"],
                },
            )

    assert rejected.status_code == 200
    assert rejected.json()["review"]["decision"] == "REJECTED"
    assert rejected.json()["packet"]["reviews"][0]["reason"].startswith("Station record")
    assert exported.status_code == 422
    assert "critical evidence remains unapproved" in exported.json()["detail"]
    assert run.status_code == 422
    assert "select at least two towns" in run.json()["detail"]
    execute.assert_not_called()


def test_research_rejection_is_idempotent_per_packet_place_and_metric(tmp_path: Path) -> None:
    with TestClient(
        create_app(tmp_path / "output", discovery_provider=FakeDiscoveryProvider()),
        base_url="http://127.0.0.1",
    ) as client:
        packet = client.post(
            "/api/research/discover",
            json={
                "preferences": (
                    "Walkable four-season retirement town with nature and a lively core."
                ),
                "exemplar_towns": ["Traverse City, MI"],
            },
        ).json()
        rejection = {
            "packet_id": packet["packet_id"],
            "reviewer": "Brett Stark",
            "place": {"place_id": "asheville_nc", "name": "Asheville", "state": "NC"},
            "metric_id": "annual_snowfall",
            "reason": "Station record cannot represent the entire town.",
        }
        first = client.post("/api/research/reject", json=rejection)
        second = client.post("/api/research/reject", json=rejection)

    assert first.status_code == 200
    assert second.status_code == 422
    assert "already been rejected" in second.json()["detail"]


def test_imported_evidence_requires_two_selected_towns(tmp_path: Path) -> None:
    evidence = Path("data/benchmarks/evidence.csv").read_bytes()
    with TestClient(create_app(tmp_path / "output"), base_url="http://127.0.0.1") as client:
        token = client.post("/api/evidence/inspect", content=evidence).json()["evidence_token"]
        with patch("lifescape.web.execute_run") as execute:
            response = client.post(
                "/api/run",
                json={
                    "selected_place_ids": ["williamsburg_va"],
                    "purchase_budget_max": 700_000,
                    "future_self_age": 75,
                    "household": "couple",
                    "evidence_token": token,
                },
            )

    assert response.status_code == 422
    assert "select at least two towns" in response.json()["detail"]
    execute.assert_not_called()


def test_research_run_requires_a_packet_from_the_current_session(tmp_path: Path) -> None:
    with (
        TestClient(create_app(tmp_path / "output"), base_url="http://127.0.0.1") as client,
        patch("lifescape.web.execute_run") as execute,
    ):
        response = client.post(
            "/api/run",
            json={
                "selected_place_ids": ["asheville_nc", "williamsburg_va"],
                "purchase_budget_max": 700_000,
                "future_self_age": 75,
                "household": "couple",
                "research_packet_id": "deadbeefcafe",
            },
        )

    assert response.status_code == 422
    assert "research packet is not available in this session" in response.json()["detail"]
    execute.assert_not_called()


def test_run_requires_imported_evidence_from_the_current_session(tmp_path: Path) -> None:
    with (
        TestClient(create_app(tmp_path / "output"), base_url="http://127.0.0.1") as client,
        patch("lifescape.web.execute_run") as execute,
    ):
        response = client.post(
            "/api/run",
            json={
                "selected_place_ids": ["asheville_nc", "williamsburg_va"],
                "purchase_budget_max": 700_000,
                "future_self_age": 75,
                "household": "couple",
                "evidence_token": "deadbeefcafe",
            },
        )

    assert response.status_code == 422
    assert "imported evidence is no longer available; import it again" in response.json()["detail"]
    execute.assert_not_called()


def test_hosted_demo_rejects_research_endpoints(tmp_path: Path) -> None:
    with TestClient(
        create_app(tmp_path / "output", hosted_demo=True),
        base_url="https://lifescape.buildproven.ai",
    ) as client:
        inspected = client.get("/api/research/packets/nope")
        selected = client.post("/api/research/select", json={})
        fetched = client.post("/api/research/fetch", json={})
        promoted = client.post("/api/research/promote", json={})
        approved = client.post("/api/research/approve-fetched", json={})
        rejected = client.post("/api/research/reject", json={})
        exported = client.get("/api/research/packets/nope/evidence.csv")

    for response in (inspected, selected, fetched, promoted, approved, rejected, exported):
        assert response.status_code == 404
        assert "hosted site has no application API" in response.json()["detail"]


def test_hosted_demo_is_synthetic_and_stateless(tmp_path: Path) -> None:
    output = tmp_path / "output"
    with TestClient(
        create_app(output, hosted_demo=True),
        base_url="https://lifescape.buildproven.ai",
    ) as client:
        page = client.get("/demo")
        bootstrap = client.get("/api/bootstrap")
        imported = client.post(
            "/api/evidence/inspect",
            content=b"private evidence",
            headers={"content-type": "text/csv"},
        )
        response = client.post(
            "/api/run",
            headers={"origin": "https://lifescape.buildproven.ai"},
            json={
                "selected_place_ids": ["williamsburg_va", "maryville_tn"],
                "purchase_budget_max": 700_000,
                "future_self_age": 75,
                "household": "couple",
            },
        )
        download = client.get("/api/downloads/000000000000/comparison.md")

    assert bootstrap.status_code == 404
    assert "Williamsburg leads this field." in page.text
    assert "stay on this computer" not in page.text
    assert imported.status_code == 404
    assert response.status_code == 404
    assert download.status_code == 404
    assert not output.exists()


@pytest.mark.parametrize(
    ("method", "path", "content"),
    [
        ("POST", "/api/run", b"{"),
        ("GET", "/api/run", None),
        ("OPTIONS", "/api/run", None),
        ("POST", "/api/bootstrap", None),
        ("GET", "/api/downloads/not-a-token/comparison.md", None),
        ("GET", "/openapi.json", None),
    ],
)
def test_hosted_api_boundary_precedes_routing_and_validation(
    tmp_path: Path,
    method: str,
    path: str,
    content: bytes | None,
) -> None:
    with TestClient(
        create_app(tmp_path / "output", hosted_demo=True),
        base_url="https://lifescape.buildproven.ai",
    ) as client:
        response = client.request(
            method,
            path,
            content=content,
            headers={"content-type": "application/json"} if content else None,
        )

    assert response.status_code == 404
    assert response.json() == {"detail": "the hosted site has no application API"}
    assert not (tmp_path / "output").exists()


def test_vercel_entrypoint_exposes_hosted_demo() -> None:
    vercel_app = runpy.run_path("api/index.py")["app"]
    with TestClient(vercel_app, base_url="https://lifescape.buildproven.ai") as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "Decide where retirement still works." in response.text


def test_hosted_guard_rejects_rotating_clients_without_retaining_them() -> None:
    guard = HostedRunGuard(enabled=True, max_concurrent=1)
    guard.acquire("active-client")

    for index in range(256):
        with pytest.raises(HTTPException) as error:
            guard.acquire(f"rejected-client-{index}")
        assert error.value.status_code == 429

    assert guard.tracked_client_count == 1
    guard.release()


def test_hosted_guard_enforces_disabled_and_per_client_limits() -> None:
    disabled = HostedRunGuard(enabled=False)
    with pytest.raises(HTTPException, match="temporarily disabled"):
        disabled.acquire("client")

    limited = HostedRunGuard(enabled=True, run_limit=1, max_concurrent=2)
    limited.acquire("client")
    with pytest.raises(HTTPException, match="limit reached"):
        limited.acquire("client")
    limited.release()

    global_limited = HostedRunGuard(enabled=True, global_run_limit=1, max_concurrent=2)
    global_limited.acquire("first")
    global_limited.release()
    with pytest.raises(HTTPException, match="capacity is exhausted"):
        global_limited.acquire("second")

    tracked_limited = HostedRunGuard(enabled=True, max_tracked_clients=1, max_concurrent=2)
    tracked_limited.acquire("first")
    tracked_limited.release()
    with pytest.raises(HTTPException, match="capacity is exhausted"):
        tracked_limited.acquire("second")


def test_local_html_preserves_local_disclosure(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "output"), base_url="http://127.0.0.1") as client:
        response = client.get("/")

    assert "Your evidence and outputs stay on this computer." in response.text
    assert "CSV uploads are disabled." not in response.text


@pytest.mark.parametrize(
    "csv_text, message",
    [("", "no header"), ("place_id,place_name,state,metric\n", "no rows")],
)
def test_evidence_reader_rejects_empty_csv_shapes(csv_text: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _read_evidence(csv_text, ("metric",))


def test_finished_demo_shows_a_completed_decision(tmp_path: Path) -> None:
    with TestClient(
        create_app(tmp_path / "output", hosted_demo=True),
        base_url="https://lifescape.buildproven.ai",
    ) as client:
        response = client.get("/demo")

    assert response.status_code == 200
    assert "evidence-backed retirement-town decision engine" in response.text
    assert "explainable shortlist" in response.text
    assert "Williamsburg leads this field." in response.text
    assert "focus deeper research" in response.text
    assert "avoid carrying forward" in response.text
    assert "fails an essential" in response.text
    assert "Hard gates cleared" in response.text
    assert "Overall fit · 0 to 10" in response.text
    assert "Top-three · 1,000 simulations" in response.text
    assert "Ranked after the non-negotiables." in response.text
    assert "top-three in at least 60% of sensitivity runs" in response.text
    assert "means less than 60%" in response.text
    assert "Blocked, not hidden" in response.text
    assert "Get Lifescape on GitHub" in response.text
    assert 'href="/"' in response.text


def test_local_demo_bookmark_redirects_to_workspace(tmp_path: Path) -> None:
    with TestClient(
        create_app(tmp_path / "output"),
        base_url="http://127.0.0.1",
        follow_redirects=False,
    ) as client:
        response = client.get("/demo")

    assert response.status_code == 307
    assert response.headers["location"] == "/"


class DemoDataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[dict[str, str]] = []
        self.active: list[tuple[str, dict[str, str], list[str]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for _, _, text in self.active:
            text.append(" ")
        values = {key: value for key, value in attrs if value is not None}
        if any(
            key in values
            for key in ("data-place-id", "data-criterion", "data-profile", "data-gates-passed")
        ):
            self.active.append((tag, values, []))

    def handle_data(self, data: str) -> None:
        for _, _, text in self.active:
            text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self.active and self.active[-1][0] == tag:
            _, values, text = self.active.pop()
            values["visible-text"] = " ".join("".join(text).split())
            self.rows.append(values)
        for _, _, text in self.active:
            text.append(" ")


def test_finished_demo_tracks_canonical_benchmark(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "output"), base_url="http://127.0.0.1") as client:
        bootstrap = client.get("/api/bootstrap").json()
        places = bootstrap["places"]
        result = client.post(
            "/api/run",
            json={
                "selected_place_ids": [place["place_id"] for place in places],
                "purchase_budget_max": 700_000,
                "future_self_age": 75,
                "household": "couple",
            },
        ).json()
    with TestClient(
        create_app(tmp_path / "hosted", hosted_demo=True),
        base_url="https://lifescape.buildproven.ai",
    ) as hosted_client:
        page = hosted_client.get("/demo")

    parser = DemoDataParser()
    parser.feed(page.text)
    ranked = {
        row["data-place-id"]: row
        for row in parser.rows
        if "data-place-id" in row and "data-score" in row
    }
    blocked = {row["data-place-id"]: row for row in parser.rows if "data-gates" in row}
    criteria = {
        row["data-criterion"]: row["data-score"] for row in parser.rows if "data-criterion" in row
    }
    profile = {
        row["data-profile"]: row["data-value"] for row in parser.rows if "data-profile" in row
    }

    assert profile["household"] == bootstrap["defaults"]["household"]
    assert profile["future_self_age"] == str(bootstrap["defaults"]["future_self_age"])
    assert profile["purchase_budget_max"] == str(bootstrap["defaults"]["purchase_budget_max"])
    assert profile["field_count"] == str(len(places))
    assert profile["metric_count"] == str(bootstrap["metric_count"])
    assert profile["sensitivity_simulations"] == str(
        signature(execute_run).parameters["simulations"].default
    )
    profile_rows = {row["data-profile"]: row for row in parser.rows if "data-profile" in row}
    assert profile_rows["household"]["visible-text"] == "Couple"
    assert profile_rows["future_self_age"]["visible-text"] == "75"
    assert profile_rows["purchase_budget_max"]["visible-text"] == "$700,000"
    assert profile_rows["field_count"]["visible-text"] == "10 towns"
    assert profile_rows["metric_count"]["visible-text"] == "17 synthetic metrics"
    assert profile_rows["sensitivity_simulations"]["visible-text"] == "1,000 simulations"
    gate_summary = next(row for row in parser.rows if "data-gates-passed" in row)
    assert gate_summary["data-gates-passed"] == str(len(result["rankings"][0]["gates"]))
    assert gate_summary["visible-text"] == (
        "Hard gates cleared 7 / 7 Overall fit · 0 to 10 6.4 Top-three · 1,000 simulations 100%"
    )
    assert "lake_geneva_wi" in ranked
    assert "Libertyville" not in page.text
    for place in result["rankings"]:
        row = ranked[place["place_id"]]
        assert row["data-score"] == str(place["score"])
        assert row["data-top-three"] == str(place["top_three_frequency"])
        assert row["data-fragile"] == str(place["fragile"]).lower()
        stability = "Fragile" if place["fragile"] else "Stable"
        assert row["visible-text"] == (
            f"{place['rank']:02} {place['name']}, {place['state']} {stability} · "
            f"{place['top_three_frequency']}% top three {place['score']}"
        )
    leader_criteria = {
        item["name"]: str(item["score"]) for item in result["rankings"][0]["criteria"]
    }
    assert criteria == {name: leader_criteria[name] for name in criteria}
    criterion_rows = {row["data-criterion"]: row for row in parser.rows if "data-criterion" in row}
    for name, score in criteria.items():
        assert criterion_rows[name]["visible-text"].casefold() == f"{name} {score}".casefold()
    for place in result["blocked"]:
        expected = "|".join(
            f"{gate['name']}:{gate['state']}:{gate['value']}:{gate['threshold']}"
            for gate in place["gates"]
        )
        assert blocked[place["place_id"]]["data-gates"] == expected
    assert blocked["beaufort_sc"]["visible-text"] == "Beaufort, SC Hazard profile 8 exceeds 7"
    assert blocked["charleston_sc"]["visible-text"] == (
        "Charleston, SC Purchase feasibility $720k exceeds $700k"
    )
    assert blocked["erie_pa"]["visible-text"] == (
        "Erie, PA Broadband + winter Unknown evidence · 89 exceeds 65"
    )
    assert blocked["muskegon_mi"]["visible-text"] == "Muskegon, MI Winter severity 95 exceeds 65"
    assert blocked["traverse_city_mi"]["visible-text"] == (
        "Traverse City, MI Winter severity 125 exceeds 65"
    )


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
        patch("lifescape.web.execute_run") as execute,
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
        f"{header}\n{first_row.replace('lake_geneva_wi', 'lake_geneva_wi"oops')}".encode()
    )
    escaped_quote = evidence.replace(",Lake Geneva,", ',"Lake Geneva ""North""",', 1).encode()

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
    lake_geneva = next(
        place for place in valid_response.json()["places"] if place["place_id"] == "lake_geneva_wi"
    )
    assert lake_geneva["name"] == 'Lake Geneva "North"'


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
        selected = ["lake_geneva_wi", "new_bern_nc"]
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
        duplicate = client.post(
            "/api/run",
            json={
                "selected_place_ids": ["williamsburg_va", "williamsburg_va"],
                "purchase_budget_max": 700_000,
                "future_self_age": 75,
                "household": "couple",
            },
        )
        response = client.post(
            "/api/run",
            json={
                "selected_place_ids": ["unknown", "also_unknown"],
                "purchase_budget_max": 700_000,
                "future_self_age": 75,
                "household": "couple",
            },
        )

    assert duplicate.status_code == 422
    assert "selected places must be unique" in duplicate.text
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
        patch("lifescape.web.execute_run", side_effect=fail_after_partial_write),
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
        patch("lifescape.web._response", side_effect=ValueError("response mismatch")),
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
