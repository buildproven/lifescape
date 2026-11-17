"""BUI-1783: MCP evidence tools with human-only approval.

Every tool is called through an in-memory MCP client session. The live connectors are stubs
that return fixed observations, so no test reaches a network. Fixture evidence is synthetic.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Awaitable, Callable, Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import Client
from mcp_types import CallToolResult, TextContent

from lifescape.connectors import Connector
from lifescape.connectors.base import DataRequest, RawResponse, ValidationResult
from lifescape.mcp_server import _census_geographies, _default_connectors, create_server
from lifescape.models import (
    Confidence,
    ObservationRecord,
    PlaceRecord,
    SourceRecord,
    SourceTier,
)
from lifescape.research import ReviewDecision, ReviewRecord
from lifescape.research_workspace import ResearchWorkspace, StoredResearchPacket

FIXTURES = Path(__file__).parent / "fixtures" / "mcp"
EVIDENCE = FIXTURES / "finalists-evidence.csv"
INVALID_EVIDENCE = FIXTURES / "invalid-evidence.csv"
NEW_BERN_MANIFEST = FIXTURES / "new-bern-manifest.csv"
LAKE_GENEVA, NEW_BERN, MARYVILLE = "5541450", "3746340", "4746380"
BROADBAND, EDUCATION, SNOWFALL = "broadband_mbps_down", "education_attainment", "annual_snowfall"
STUB_SOURCE_URL = "https://data.census.gov/stub/acs"


def _stub_observation(metric_id: str, value: float) -> ObservationRecord:
    today = date.today()
    return ObservationRecord(
        place=PlaceRecord(place_id="connector-native", name="Stub", state="ZZ"),
        metric_id=metric_id,
        raw_value=value,
        observed_period="2020-2024",
        observed_at=today - timedelta(days=30),
        source=SourceRecord(
            url=STUB_SOURCE_URL,
            title=f"Stub ACS {metric_id}",
            publisher="U.S. Census Bureau",
            tier=SourceTier.A,
            retrieved_at=today,
            geography="town",
            confidence=Confidence.HIGH,
            synthetic=False,
        ),
    )


class StubCensus:
    """Stands in for census_acs; returns more metrics than requested, like the real one."""

    name = "census_acs"
    supported_metric_ids = (BROADBAND, EDUCATION)

    def __init__(self, requests: list[DataRequest], *, fail: bool = False) -> None:
        self.requests = requests
        self.fail = fail

    def fetch(self, request: DataRequest) -> RawResponse:
        self.requests.append(request)
        if self.fail:
            raise OSError("census API unreachable")
        return RawResponse(source_url=STUB_SOURCE_URL, payload=b"{}", checksum="stub")

    def normalize(self, response: RawResponse) -> list[ObservationRecord]:
        del response
        return [_stub_observation(BROADBAND, 250.0), _stub_observation(EDUCATION, 31.0)]

    def validate(self, observations: list[ObservationRecord]) -> ValidationResult:
        del observations
        return ValidationResult(valid=True)


class StubNoaa:
    """Stands in for noaa_gsoy; must never run, because no station geography is configured."""

    name = "noaa_gsoy"
    supported_metric_ids = (SNOWFALL,)

    def fetch(self, request: DataRequest) -> RawResponse:
        raise AssertionError(f"NOAA fetched without a configured station: {request}")

    def normalize(self, response: RawResponse) -> list[ObservationRecord]:
        raise AssertionError("NOAA normalized without a configured station")

    def validate(self, observations: list[ObservationRecord]) -> ValidationResult:
        raise AssertionError("NOAA validated without a configured station")


def _connectors(
    requests: list[DataRequest], *, fail: bool = False
) -> Callable[[], Sequence[Connector]]:
    def build() -> Sequence[Connector]:
        return (StubCensus(requests, fail=fail), StubNoaa())

    return build


def _session[T](
    output_dir: Path,
    body: Callable[[Client], Awaitable[T]],
    requests: list[DataRequest] | None = None,
    *,
    fail: bool = False,
) -> T:
    async def main() -> T:
        server = create_server(
            output_dir=output_dir,
            live_connectors=_connectors([] if requests is None else requests, fail=fail),
        )
        async with Client(server, mode="legacy") as client:
            return await body(client)

    return anyio.run(main)


def _call(
    output_dir: Path,
    tool: str,
    arguments: dict[str, Any],
    requests: list[DataRequest] | None = None,
    *,
    fail: bool = False,
) -> CallToolResult:
    async def body(client: Client) -> CallToolResult:
        return await client.call_tool(tool, arguments)

    return _session(output_dir, body, requests, fail=fail)


def _ok(result: CallToolResult) -> dict[str, Any]:
    assert not result.is_error, _text(result)
    assert isinstance(result.structured_content, dict)
    return result.structured_content


def _text(result: CallToolResult) -> str:
    return "\n".join(item.text for item in result.content if isinstance(item, TextContent))


def _stored(output_dir: Path, packet_id: str) -> StoredResearchPacket:
    packets = ResearchWorkspace(output_dir / "research-workspace").load()
    return next(item for item in packets if item.packet.id == packet_id)


def _fetch(output_dir: Path, metric_ids: list[str] | None = None) -> dict[str, Any]:
    return _ok(
        _call(
            output_dir,
            "fetch_live_evidence",
            {"place_ids": [NEW_BERN, LAKE_GENEVA], "metric_ids": metric_ids or [BROADBAND]},
        )
    )


def _approve(output_dir: Path, packet_id: str, reviewer: str) -> CallToolResult:
    return _call(
        output_dir,
        "approve_evidence",
        {
            "packet_id": packet_id,
            "place_id": NEW_BERN,
            "metric_id": BROADBAND,
            "reviewer": reviewer,
        },
    )


def _compare(output_dir: Path, as_of: str) -> dict[str, Any]:
    return _ok(
        _call(
            output_dir,
            "compare_finalists",
            {"place_ids": [LAKE_GENEVA, NEW_BERN], "evidence_path": str(EVIDENCE), "as_of": as_of},
        )
    )


def test_req_mcp_5_6_7_8_lists_the_evidence_tools(tmp_path: Path) -> None:
    async def body(client: Client) -> dict[str, dict[str, Any]]:
        listed = await client.list_tools()
        return {tool.name: tool.input_schema for tool in listed.tools}

    schemas = _session(tmp_path, body)

    for name in ("inspect_evidence", "fetch_live_evidence", "approve_evidence", "export_report"):
        assert schemas[name]["additionalProperties"] is False, name
    assert set(schemas["approve_evidence"]["required"]) == {
        "packet_id",
        "place_id",
        "metric_id",
        "reviewer",
    }


# REQ-MCP-5 ---------------------------------------------------------------------------------


def test_req_mcp_5_inspect_evidence_summarizes_each_town_and_metric(tmp_path: Path) -> None:
    payload = _ok(
        _call(
            tmp_path,
            "inspect_evidence",
            {
                "evidence_path": str(EVIDENCE),
                "manifest_path": str(NEW_BERN_MANIFEST),
                "as_of": "2026-01-01",
            },
        )
    )

    assert payload["valid"] is True
    assert payload["errors"] == []
    assert payload["as_of"] == "2026-01-01"
    towns = {town["place_id"]: town for town in payload["towns"]}
    assert set(towns) == {LAKE_GENEVA, NEW_BERN, MARYVILLE}
    new_bern = towns[NEW_BERN]
    assert new_bern["town"] == "New Bern, NC"
    statuses = {metric["metric_id"]: metric for metric in new_bern["metrics"]}
    assert statuses["median_sale_price"]["status"] == "ready"
    assert statuses["median_sale_price"]["raw_value"] == 330000
    assert statuses["median_sale_price"]["validated_provenance"]["source_url"].endswith(
        "/median_sale_price"
    )
    assert new_bern["ready_count"] + new_bern["finding_count"] == len(new_bern["metrics"])
    lake_geneva = towns[LAKE_GENEVA]
    assert lake_geneva["ready_count"] == 0
    assert {metric["reason"] for metric in lake_geneva["metrics"]} == {
        "missing metric-specific provenance record"
    }
    assert payload["observation_count"] == sum(len(t["metrics"]) for t in payload["towns"])
    assert payload["finding_count"] == sum(t["finding_count"] for t in payload["towns"])
    maryville = towns[MARYVILLE]
    assert BROADBAND in maryville["missing_metrics"]


def test_req_mcp_5_inspect_evidence_returns_validation_errors(tmp_path: Path) -> None:
    payload = _ok(_call(tmp_path, "inspect_evidence", {"evidence_path": str(INVALID_EVIDENCE)}))

    assert payload["valid"] is False
    assert len(payload["errors"]) == 1
    assert BROADBAND in payload["errors"][0]
    assert "outside valid range" in payload["errors"][0]
    (town,) = payload["towns"]
    assert town["place_id"] == NEW_BERN


def test_req_mcp_5_inspect_evidence_reports_an_unreadable_layout(tmp_path: Path) -> None:
    broken = tmp_path / "broken.csv"
    broken.write_text("place_id,vibe\n3746340,coastal\n", encoding="utf-8")

    payload = _ok(_call(tmp_path, "inspect_evidence", {"evidence_path": str(broken)}))

    assert payload["valid"] is False
    assert len(payload["errors"]) == 2
    assert all("missing columns" in error for error in payload["errors"])
    assert payload["towns"] == []
    assert payload["observation_count"] == 0


def test_req_mcp_5_inspect_evidence_reports_a_missing_file(tmp_path: Path) -> None:
    result = _call(tmp_path, "inspect_evidence", {"evidence_path": str(tmp_path / "none.csv")})

    assert result.is_error
    assert "evidence CSV not found" in _text(result)


def test_req_mcp_5_inspect_evidence_reports_a_missing_manifest(tmp_path: Path) -> None:
    result = _call(
        tmp_path,
        "inspect_evidence",
        {"evidence_path": str(EVIDENCE), "manifest_path": str(tmp_path / "none.csv")},
    )

    assert result.is_error
    assert "manifest not found" in _text(result)


# REQ-MCP-6 ---------------------------------------------------------------------------------


def test_req_mcp_6_fetch_live_evidence_proposes_unapproved_observations(tmp_path: Path) -> None:
    requests: list[DataRequest] = []
    payload = _ok(
        _call(
            tmp_path,
            "fetch_live_evidence",
            {"place_ids": [NEW_BERN, LAKE_GENEVA], "metric_ids": [BROADBAND]},
            requests,
        )
    )

    assert sorted(request.geography for request in requests) == ["37:46340", "55:41450"]
    observations = payload["observations"]
    assert {(item["place_id"], item["metric_id"]) for item in observations} == {
        (NEW_BERN, BROADBAND),
        (LAKE_GENEVA, BROADBAND),
    }
    for item in observations:
        assert item["approved"] is False
        assert item["raw_value"] == 250.0
        assert item["source"]["url"] == STUB_SOURCE_URL
        assert item["source"]["tier"] == "A"
        assert item["source"]["retrieved_at"] == date.today().isoformat()
    towns = {item["place_id"]: item["town"] for item in observations}
    assert towns[NEW_BERN] == "New Bern, NC"
    assert payload["missing"] == []
    assert payload["errors"] == {}
    assert "not evidence" in payload["evidence_status"]

    stored = _stored(tmp_path, payload["packet_id"])
    assert stored.promotions == ()
    assert stored.reviews == ()
    assert len(stored.fetch_history[-1].result.observations) == 2


def test_req_mcp_6_fetch_lists_missing_geography_instead_of_guessing(tmp_path: Path) -> None:
    payload = _fetch(tmp_path, [SNOWFALL])

    assert payload["observations"] == []
    assert {(item["place_id"], item["metric_id"]) for item in payload["missing"]} == {
        (NEW_BERN, SNOWFALL),
        (LAKE_GENEVA, SNOWFALL),
    }
    assert payload["errors"][NEW_BERN] == ["noaa_gsoy: explicit source geography is not configured"]


def test_req_mcp_6_fetch_reports_a_connector_failure(tmp_path: Path) -> None:
    payload = _ok(
        _call(
            tmp_path,
            "fetch_live_evidence",
            {"place_ids": [NEW_BERN], "metric_ids": [BROADBAND]},
            fail=True,
        )
    )

    assert payload["observations"] == []
    assert payload["missing"] == [{"place_id": NEW_BERN, "metric_id": BROADBAND}]
    assert payload["errors"] == {NEW_BERN: ["census_acs: census API unreachable"]}


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"place_ids": [NEW_BERN], "metric_ids": ["vibe_score"]}, "unknown metric IDs"),
        (
            {"place_ids": [NEW_BERN], "metric_ids": ["median_sale_price"]},
            "no live connector fetches metrics",
        ),
        ({"place_ids": ["0000000"], "metric_ids": [BROADBAND]}, "not in the catalog"),
        ({"place_ids": [NEW_BERN, NEW_BERN], "metric_ids": [BROADBAND]}, "distinct"),
        ({"place_ids": [NEW_BERN], "metric_ids": [BROADBAND, BROADBAND]}, "distinct"),
    ],
)
def test_req_mcp_6_fetch_rejects_unusable_requests(
    tmp_path: Path, arguments: dict[str, Any], message: str
) -> None:
    requests: list[DataRequest] = []
    result = _call(tmp_path, "fetch_live_evidence", arguments, requests)

    assert result.is_error
    assert message in _text(result)
    assert requests == []
    assert not (tmp_path / "research-workspace").exists()


# REQ-MCP-7 ---------------------------------------------------------------------------------


def test_req_mcp_7_approve_evidence_is_annotated_destructive(tmp_path: Path) -> None:
    async def body(client: Client) -> dict[str, Any]:
        listed = await client.list_tools()
        return {tool.name: tool.annotations for tool in listed.tools}

    annotations = _session(tmp_path, body)

    approve = annotations["approve_evidence"]
    assert approve is not None
    assert approve.destructive_hint is True
    assert approve.read_only_hint is False
    for name in ("inspect_evidence", "export_report"):
        assert annotations[name] is not None
        assert annotations[name].read_only_hint is True, name


def test_req_mcp_7_approval_by_a_named_human_promotes_the_evidence(tmp_path: Path) -> None:
    packet_id = _fetch(tmp_path)["packet_id"]

    payload = _ok(_approve(tmp_path, packet_id, "Brett"))

    assert payload["reviewer"] == "Brett"
    assert payload["approved"]["approved"] is True
    assert payload["approved"]["place_id"] == NEW_BERN
    assert payload["approved"]["metric_id"] == BROADBAND
    assert payload["approved"]["source"]["url"] == STUB_SOURCE_URL
    assert payload["state"] == "RESEARCHING"
    assert BROADBAND not in payload["critical_metrics_needed"][NEW_BERN]
    assert BROADBAND in payload["critical_metrics_needed"][LAKE_GENEVA]
    stored = _stored(tmp_path, packet_id)
    (promotion,) = stored.promotions
    assert promotion.reviewer == "Brett"
    assert promotion.observation.place.place_id == NEW_BERN
    (review,) = stored.reviews
    assert (review.reviewer, review.decision.value) == ("Brett", "APPROVED")


@pytest.mark.parametrize("reviewer", ["claude", "Claude ", "AI", "codex", "model", "", "  "])
def test_req_mcp_7_rejects_an_ai_or_empty_reviewer_and_promotes_nothing(
    tmp_path: Path, reviewer: str
) -> None:
    packet_id = _fetch(tmp_path)["packet_id"]

    result = _approve(tmp_path, packet_id, reviewer)

    assert result.is_error
    assert "nothing promoted" in _text(result)
    assert "reviewer" in _text(result)
    stored = _stored(tmp_path, packet_id)
    assert stored.promotions == ()
    assert stored.reviews == ()


def test_req_mcp_7_refuses_a_second_approval(tmp_path: Path) -> None:
    packet_id = _fetch(tmp_path)["packet_id"]
    _ok(_approve(tmp_path, packet_id, "Brett"))

    again = _approve(tmp_path, packet_id, "Brett")

    assert again.is_error
    assert "already been approved" in _text(again)
    assert len(_stored(tmp_path, packet_id).promotions) == 1


def test_req_mcp_7_refuses_an_observation_that_was_not_fetched(tmp_path: Path) -> None:
    packet_id = _fetch(tmp_path, [SNOWFALL])["packet_id"]

    result = _call(
        tmp_path,
        "approve_evidence",
        {"packet_id": packet_id, "place_id": NEW_BERN, "metric_id": SNOWFALL, "reviewer": "Brett"},
    )

    assert result.is_error
    assert "not available" in _text(result)
    assert _stored(tmp_path, packet_id).promotions == ()


def test_req_mcp_7_refuses_rejected_or_unfetched_stored_evidence(tmp_path: Path) -> None:
    packet_id = _fetch(tmp_path)["packet_id"]
    workspace = ResearchWorkspace(tmp_path / "research-workspace")
    stored = _stored(tmp_path, packet_id)
    rejection = ReviewRecord(
        packet_id=packet_id,
        place_id=NEW_BERN,
        metric_id=BROADBAND,
        reviewer="Brett",
        decision=ReviewDecision.REJECTED,
        reason="value does not match the source table",
    )
    workspace.save(stored.model_copy(update={"reviews": (rejection,)}))

    rejected = _approve(tmp_path, packet_id, "Brett")
    assert rejected.is_error
    assert "already been rejected" in _text(rejected)

    workspace.save(stored.model_copy(update={"fetch_history": ()}))
    unfetched = _approve(tmp_path, packet_id, "Brett")
    assert unfetched.is_error
    assert "not available" in _text(unfetched)
    assert _stored(tmp_path, packet_id).promotions == ()


def test_req_mcp_7_refuses_an_unknown_packet(tmp_path: Path) -> None:
    result = _approve(tmp_path, "0123456789ab", "Brett")

    assert result.is_error
    assert "not found" in _text(result)

    malformed = _approve(tmp_path, "../../etc/pw", "Brett")
    assert malformed.is_error


# REQ-MCP-8 ---------------------------------------------------------------------------------


def test_req_mcp_8_export_report_returns_the_run_report_paths(tmp_path: Path) -> None:
    compared = _compare(tmp_path, "2026-01-01")

    payload = _ok(_call(tmp_path, "export_report", {"run_id": compared["run_id"]}))

    assert payload["run_id"] == compared["run_id"]
    assert payload["reports"] == compared["reports"]
    reports = payload["reports"]
    assert Path(reports["markdown"]).suffix == ".md"
    assert Path(reports["ranking_csv"]).suffix == ".csv"
    assert Path(reports["sensitivity_csv"]).suffix == ".csv"
    assert Path(reports["database"]).suffix == ".sqlite"
    for path in reports.values():
        assert Path(path).is_file(), path
    assert compared["run_id"] in Path(reports["markdown"]).read_text(encoding="utf-8")


def test_req_mcp_8_refuses_reports_a_later_run_overwrote(tmp_path: Path) -> None:
    first = _compare(tmp_path, "2026-01-01")
    second = _compare(tmp_path, "2026-01-02")
    assert first["run_id"] != second["run_id"]
    assert first["reports"] == second["reports"]

    stale = _call(tmp_path, "export_report", {"run_id": first["run_id"]})

    assert stale.is_error
    assert "overwritten by a later run" in _text(stale)
    assert _ok(_call(tmp_path, "export_report", {"run_id": second["run_id"]}))["reports"]


def test_req_mcp_8_refuses_an_unknown_or_malformed_run(tmp_path: Path) -> None:
    unknown = _call(tmp_path, "export_report", {"run_id": "0123456789abcdef"})
    assert unknown.is_error
    assert "call compare_finalists first" in _text(unknown)

    malformed = _call(tmp_path, "export_report", {"run_id": "../runs"})
    assert malformed.is_error


def test_req_mcp_8_refuses_a_damaged_report_index(tmp_path: Path) -> None:
    compared = _compare(tmp_path, "2026-01-01")
    index = tmp_path / "runs" / f"{compared['run_id']}.json"
    other = tmp_path / "runs" / "fedcba9876543210.json"
    other.write_text(index.read_text(encoding="utf-8"), encoding="utf-8")

    renamed = _call(tmp_path, "export_report", {"run_id": "fedcba9876543210"})
    assert renamed.is_error
    assert "not fedcba9876543210" in _text(renamed)

    index.write_text("{not json", encoding="utf-8")
    damaged = _call(tmp_path, "export_report", {"run_id": compared["run_id"]})
    assert damaged.is_error


def test_req_mcp_8_refuses_a_missing_report_file(tmp_path: Path) -> None:
    compared = _compare(tmp_path, "2026-01-01")
    Path(compared["reports"]["markdown"]).unlink()

    result = _call(tmp_path, "export_report", {"run_id": compared["run_id"]})

    assert result.is_error
    assert "markdown report" in _text(result)


def test_req_mcp_8_refuses_a_database_without_the_run(tmp_path: Path) -> None:
    compared = _compare(tmp_path, "2026-01-01")
    with sqlite3.connect(compared["reports"]["database"]) as connection:
        connection.execute("DELETE FROM research_runs WHERE run_id = ?", (compared["run_id"],))

    result = _call(tmp_path, "export_report", {"run_id": compared["run_id"]})

    assert result.is_error
    assert "is not in its database" in _text(result)


def test_req_mcp_8_reports_an_unreadable_database(tmp_path: Path) -> None:
    compared = _compare(tmp_path, "2026-01-01")
    Path(compared["reports"]["database"]).write_bytes(b"not a database")

    result = _call(tmp_path, "export_report", {"run_id": compared["run_id"]})

    assert result.is_error
    assert "cannot read the run database" in _text(result)


def test_req_mcp_6_derives_census_geography_only_from_a_place_geoid() -> None:
    assert _census_geographies(NEW_BERN) == {"census_acs": "37:46340"}
    assert _census_geographies("new_bern_nc") == {}
    assert _census_geographies("374634") == {}
    assert {connector.name for connector in _default_connectors()} == {"census_acs", "noaa_gsoy"}
