"""BUI-2022: export_report returns the report text kept in the store.

The database adapter runs on a SQLite stand-in (an SQLAlchemy ``sqlite:///`` URL in
``tmp_path``), so no test needs a database server or a network. The live Census connector is a
stub; fixture evidence is synthetic.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from collections.abc import Callable, Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import anyio
from mcp import Client
from mcp_types import CallToolResult, TextContent

from lifescape.connectors import Connector
from lifescape.connectors.base import DataRequest, RawResponse, ValidationResult
from lifescape.mcp_server import create_server
from lifescape.models import Confidence, ObservationRecord, PlaceRecord, SourceRecord, SourceTier
from lifescape.store import LocalStore, RunReportIndex, Store
from lifescape.store_db import DatabaseStore

FIXTURES = Path(__file__).parent / "fixtures" / "mcp"
EVIDENCE = FIXTURES / "finalists-evidence.csv"
LAKE_GENEVA, NEW_BERN = "5541450", "3746340"
CRITICAL_VALUES = {
    "median_sale_price": 320000.0,
    "er_drive_minutes": 10.0,
    "broadband_mbps_down": 400.0,
    "annual_snowfall": 2.0,
    "flood_risk_score": 5.0,
    "distress_index": 3.0,
    "one_level_inventory_count": 40.0,
}
STUB_SOURCE_URL = "https://data.census.gov/stub/acs"
LEGACY_RUN = "0123456789abcdef"
CSV_NAMES = {"ranking_csv", "sensitivity_csv"}


class StubCensus:
    """Stands in for census_acs so no test reaches the network."""

    name = "census_acs"
    supported_metric_ids = tuple(CRITICAL_VALUES)

    def fetch(self, request: DataRequest) -> RawResponse:
        del request
        return RawResponse(source_url=STUB_SOURCE_URL, payload=b"{}", checksum="stub")

    def normalize(self, response: RawResponse) -> list[ObservationRecord]:
        del response
        today = date.today()
        return [
            ObservationRecord(
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
            for metric_id, value in CRITICAL_VALUES.items()
        ]

    def validate(self, observations: list[ObservationRecord]) -> ValidationResult:
        del observations
        return ValidationResult(valid=True)


Step = tuple[str, dict[str, Any] | Callable[[list[CallToolResult]], dict[str, Any]]]


def _calls(output_dir: Path, store: Store, steps: Sequence[Step]) -> list[CallToolResult]:
    """Run tool calls in one session of a new server; a callable step builds its arguments."""

    def connectors() -> Sequence[Connector]:
        return (StubCensus(),)

    async def main() -> list[CallToolResult]:
        server = create_server(output_dir=output_dir, live_connectors=connectors, store=store)
        results: list[CallToolResult] = []
        async with Client(server, mode="legacy") as client:
            for tool, arguments in steps:
                built = arguments(results) if callable(arguments) else arguments
                results.append(await client.call_tool(tool, built))
        return results

    return anyio.run(main)


def _ok(result: CallToolResult) -> dict[str, Any]:
    assert not result.is_error, _text(result)
    assert isinstance(result.structured_content, dict)
    return result.structured_content


def _text(result: CallToolResult) -> str:
    return "\n".join(item.text for item in result.content if isinstance(item, TextContent))


def _sqlite_url(path: Path) -> str:
    return f"sqlite:///{path}"


def _approve(place_id: str, metric_id: str) -> Callable[[list[CallToolResult]], dict[str, Any]]:
    def arguments(results: list[CallToolResult]) -> dict[str, Any]:
        return {
            "packet_id": _ok(results[0])["packet_id"],
            "place_id": place_id,
            "metric_id": metric_id,
            "reviewer": "Brett",
        }

    return arguments


def _compare_on_packet(output_dir: Path, store: Store) -> dict[str, Any]:
    """Fetch, approve every critical value, and compare the finalists on the packet."""
    steps: list[Step] = [
        (
            "fetch_live_evidence",
            {"place_ids": [LAKE_GENEVA, NEW_BERN], "metric_ids": list(CRITICAL_VALUES)},
        ),
        *[
            ("approve_evidence", _approve(place_id, metric_id))
            for place_id in (LAKE_GENEVA, NEW_BERN)
            for metric_id in CRITICAL_VALUES
        ],
        (
            "compare_finalists",
            lambda results: {
                "place_ids": [LAKE_GENEVA, NEW_BERN],
                "packet_id": _ok(results[0])["packet_id"],
            },
        ),
    ]
    return _ok(_calls(output_dir, store, steps)[-1])


def _export(output_dir: Path, store: Store, run_id: str) -> CallToolResult:
    return _calls(output_dir, store, [("export_report", {"run_id": run_id})])[0]


def _report_texts(reports: dict[str, str]) -> dict[str, str]:
    return {
        key: Path(path).read_text(encoding="utf-8")
        for key, path in reports.items()
        if key != "database"
    }


def _legacy_document(run_id: str) -> str:
    """A report index as saved before BUI-2022: paths and digests, no report text."""
    return json.dumps(
        {
            "run_id": run_id,
            "reports": {"markdown": "/gone/reports/comparison.md"},
            "sha256": {"markdown": "a" * 64},
        }
    )


# REQ-RPT-1 ---------------------------------------------------------------------------------


def test_req_rpt_1_compare_saves_report_text_in_the_database_store(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path / "hosted.sqlite")
    compared = _compare_on_packet(tmp_path / "server-a", DatabaseStore.from_url(url))
    written = _report_texts(compared["reports"])

    index = DatabaseStore.from_url(url).get_run_index(compared["run_id"])

    assert index is not None
    assert index.contents == written
    assert set(written) == {"markdown", *CSV_NAMES}
    assert compared["run_id"] in written["markdown"]


def test_req_rpt_1_compare_saves_report_text_in_the_local_store(tmp_path: Path) -> None:
    store = LocalStore(tmp_path)
    compared = _ok(
        _calls(
            tmp_path,
            store,
            [
                (
                    "compare_finalists",
                    {
                        "place_ids": [LAKE_GENEVA, NEW_BERN],
                        "evidence_path": str(EVIDENCE),
                        "as_of": "2026-01-01",
                    },
                )
            ],
        )[0]
    )

    index = LocalStore(tmp_path).get_run_index(compared["run_id"])

    assert index is not None
    assert index.contents == _report_texts(compared["reports"])


# REQ-RPT-2 ---------------------------------------------------------------------------------


def test_req_rpt_2_fresh_server_on_the_same_database_returns_the_report_text(
    tmp_path: Path,
) -> None:
    url = _sqlite_url(tmp_path / "hosted.sqlite")
    compared = _compare_on_packet(tmp_path / "server-a", DatabaseStore.from_url(url))
    written = _report_texts(compared["reports"])
    work_dir = Path(compared["reports"]["markdown"]).parent.parent
    assert work_dir.is_relative_to(tmp_path / "server-a")
    shutil.rmtree(work_dir)

    exported = _ok(_export(tmp_path / "server-b", DatabaseStore.from_url(url), compared["run_id"]))

    assert exported["run_id"] == compared["run_id"]
    assert exported["markdown"] == written["markdown"]
    assert exported["csv"] == {name: written[name] for name in CSV_NAMES}
    assert not (tmp_path / "server-b").exists()


# REQ-RPT-3 ---------------------------------------------------------------------------------


def test_req_rpt_3_legacy_database_row_loads_and_export_says_to_compare_again(
    tmp_path: Path,
) -> None:
    path = tmp_path / "hosted.sqlite"
    store = DatabaseStore.from_url(_sqlite_url(path))
    assert store.get_run_index(LEGACY_RUN) is None  # creates the tables
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO run_report_indexes (run_id, document) VALUES (?, ?)",
            (LEGACY_RUN, _legacy_document(LEGACY_RUN)),
        )

    loaded = DatabaseStore.from_url(_sqlite_url(path)).get_run_index(LEGACY_RUN)
    result = _export(tmp_path / "server", DatabaseStore.from_url(_sqlite_url(path)), LEGACY_RUN)

    assert loaded is not None
    assert loaded.contents is None
    assert result.is_error
    assert "run compare_finalists again" in _text(result)


def test_req_rpt_3_legacy_local_index_loads_and_export_says_to_compare_again(
    tmp_path: Path,
) -> None:
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / f"{LEGACY_RUN}.json").write_text(_legacy_document(LEGACY_RUN), encoding="utf-8")

    loaded = LocalStore(tmp_path).get_run_index(LEGACY_RUN)
    result = _export(tmp_path, LocalStore(tmp_path), LEGACY_RUN)

    assert loaded == RunReportIndex.model_validate_json(_legacy_document(LEGACY_RUN))
    assert loaded is not None
    assert loaded.contents is None
    assert result.is_error
    assert "run compare_finalists again" in _text(result)


# REQ-RPT-4 ---------------------------------------------------------------------------------


def test_req_rpt_4_local_store_export_returns_text_and_report_paths(tmp_path: Path) -> None:
    store = LocalStore(tmp_path)
    compare: Step = (
        "compare_finalists",
        {
            "place_ids": [LAKE_GENEVA, NEW_BERN],
            "evidence_path": str(EVIDENCE),
            "as_of": "2026-01-01",
        },
    )
    compared, exported_result = _calls(
        tmp_path,
        store,
        [compare, ("export_report", lambda results: {"run_id": _ok(results[0])["run_id"]})],
    )

    reports = _ok(compared)["reports"]
    exported = _ok(exported_result)
    written = _report_texts(reports)
    assert exported["reports"] == reports
    assert Path(exported["reports"]["database"]).is_file()
    assert exported["markdown"] == written["markdown"]
    assert exported["csv"] == {name: written[name] for name in CSV_NAMES}


def test_req_rpt_3_index_without_markdown_text_says_to_compare_again(tmp_path: Path) -> None:
    store = DatabaseStore.from_url(_sqlite_url(tmp_path / "hosted.sqlite"))
    store.save_run_index(
        RunReportIndex(
            run_id=LEGACY_RUN,
            reports={"ranking_csv": "/gone/reports/comparison.csv"},
            sha256={"ranking_csv": "a" * 64},
            contents={"ranking_csv": "rank,place_id\n"},
        )
    )

    result = _export(tmp_path / "server", store, LEGACY_RUN)

    assert result.is_error
    assert "run compare_finalists again" in _text(result)
