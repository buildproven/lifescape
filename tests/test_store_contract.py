"""BUI-1904: discovery and evidence records go through one store with two adapters.

The contract suite runs against both adapters. The database adapter runs on a SQLite stand-in
(an SQLAlchemy ``sqlite:///`` URL in ``tmp_path``), so no test needs a database server or a
network. The MCP tests use a stub Census connector; fixture evidence is synthetic.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import Client
from mcp_types import CallToolResult, TextContent

from lifescape.connectors import Connector
from lifescape.connectors.base import DataRequest, RawResponse, ValidationResult
from lifescape.mcp_server import create_server
from lifescape.models import Confidence, ObservationRecord, PlaceRecord, SourceRecord, SourceTier
from lifescape.research import (
    DiscoveryLead,
    ResearchPacket,
    ReviewDecision,
    ReviewRecord,
    SearchBrief,
)
from lifescape.research_sources import EvidenceFetchResult
from lifescape.research_workspace import FetchSnapshot, StoredResearchPacket
from lifescape.store import (
    DATABASE_URL_ENV,
    STORE_ENV,
    LocalStore,
    RunReportIndex,
    Store,
    StoreConfigError,
    StoreError,
    StoreUnavailableError,
    store_from_env,
)
from lifescape.store_db import DatabaseStore

FIXTURES = Path(__file__).parent / "fixtures" / "mcp"
EVIDENCE = FIXTURES / "finalists-evidence.csv"
LAKE_GENEVA, NEW_BERN = "5541450", "3746340"
BROADBAND, EDUCATION = "broadband_mbps_down", "education_attainment"
STUB_SOURCE_URL = "https://data.census.gov/stub/acs"
BRIEF = SearchBrief(preferences="Synthetic store contract brief for BUI-1904 tests.")
ADAPTERS = ("local", "database")
FETCHED_AT = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def _sqlite_url(path: Path) -> str:
    return f"sqlite:///{path}"


def _unreachable_url(tmp_path: Path) -> str:
    """A SQLite file in a directory that does not exist: every connection attempt fails."""
    return _sqlite_url(tmp_path / "missing-directory" / "store.sqlite")


def _build(kind: str, tmp_path: Path) -> Store:
    if kind == "local":
        return LocalStore(tmp_path / "local-output")
    return DatabaseStore.from_url(_sqlite_url(tmp_path / "store.sqlite"))


@pytest.fixture(params=ADAPTERS)
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Store:
    return _build(str(request.param), tmp_path)


def _packet(packet_id: str, *names: str) -> StoredResearchPacket:
    leads = tuple(
        DiscoveryLead(
            place=PlaceRecord(place_id=f"{index:07d}", name=name, state="NC"),
            rationale=f"Synthetic lead {name}.",
        )
        for index, name in enumerate(names or ("New Bern",))
    )
    return StoredResearchPacket(
        packet=ResearchPacket(id=packet_id, brief=BRIEF, leads=leads, discovery_provider="test"),
        fetch_history=(
            FetchSnapshot(
                fetched_at=FETCHED_AT, result=EvidenceFetchResult(observations=(), errors={})
            ),
        ),
    )


def _index(run_id: str, marker: str = "a") -> RunReportIndex:
    return RunReportIndex(
        run_id=run_id,
        reports={"markdown": f"/reports/{marker}/comparison.md"},
        sha256={"markdown": marker * 64},
    )


# REQ-STORE-1 -------------------------------------------------------------------------------


def test_req_store_1_env_selects_the_local_adapter_by_default(tmp_path: Path) -> None:
    unset = store_from_env(tmp_path, {})
    named = store_from_env(tmp_path, {STORE_ENV: "local"})

    assert isinstance(unset, LocalStore)
    assert isinstance(named, LocalStore)
    assert "local store" in unset.label


def test_req_store_1_env_selects_the_database_adapter(tmp_path: Path) -> None:
    selected = store_from_env(
        tmp_path,
        {STORE_ENV: "database", DATABASE_URL_ENV: _sqlite_url(tmp_path / "store.sqlite")},
    )

    assert isinstance(selected, DatabaseStore)
    assert "database store" in selected.label


@pytest.mark.parametrize(
    ("environ", "message"),
    [
        ({STORE_ENV: "postgres"}, "must be 'local' or 'database'"),
        ({STORE_ENV: ""}, "must be 'local' or 'database'"),
        ({STORE_ENV: "database"}, DATABASE_URL_ENV),
        ({STORE_ENV: "database", DATABASE_URL_ENV: "  "}, DATABASE_URL_ENV),
        ({STORE_ENV: "database", DATABASE_URL_ENV: "not a url"}, "database store"),
        ({STORE_ENV: "database", DATABASE_URL_ENV: "nosuchdialect://host/db"}, "database store"),
    ],
)
def test_req_store_1_rejects_an_unusable_store_setting(
    tmp_path: Path, environ: dict[str, str], message: str
) -> None:
    with pytest.raises(StoreConfigError, match=message):
        store_from_env(tmp_path, environ)


def test_req_store_1_server_refuses_to_start_on_a_bad_store_setting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(STORE_ENV, "database")
    monkeypatch.delenv(DATABASE_URL_ENV, raising=False)

    with pytest.raises(StoreConfigError, match=DATABASE_URL_ENV):
        create_server(output_dir=tmp_path)


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
    """Stands in for census_acs so no test reaches the network."""

    name = "census_acs"
    supported_metric_ids = (BROADBAND, EDUCATION)

    def __init__(self, requests: list[DataRequest]) -> None:
        self.requests = requests

    def fetch(self, request: DataRequest) -> RawResponse:
        self.requests.append(request)
        return RawResponse(source_url=STUB_SOURCE_URL, payload=b"{}", checksum="stub")

    def normalize(self, response: RawResponse) -> list[ObservationRecord]:
        del response
        return [_stub_observation(BROADBAND, 250.0), _stub_observation(EDUCATION, 31.0)]

    def validate(self, observations: list[ObservationRecord]) -> ValidationResult:
        del observations
        return ValidationResult(valid=True)


def _calls(
    output_dir: Path,
    steps: Sequence[tuple[str, dict[str, Any] | Callable[[list[CallToolResult]], dict[str, Any]]]],
    requests: list[DataRequest] | None = None,
) -> list[CallToolResult]:
    """Run tool calls in one session; a callable step builds its arguments from earlier results."""
    seen: list[DataRequest] = [] if requests is None else requests

    def connectors() -> Sequence[Connector]:
        return (StubCensus(seen),)

    async def main() -> list[CallToolResult]:
        server = create_server(output_dir=output_dir, live_connectors=connectors)
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


FETCH = ("fetch_live_evidence", {"place_ids": [NEW_BERN], "metric_ids": [BROADBAND]})
COMPARE = (
    "compare_finalists",
    {"place_ids": [LAKE_GENEVA, NEW_BERN], "evidence_path": str(EVIDENCE), "as_of": "2026-01-01"},
)


def _approve_first(results: list[CallToolResult]) -> dict[str, Any]:
    return {
        "packet_id": _ok(results[0])["packet_id"],
        "place_id": NEW_BERN,
        "metric_id": BROADBAND,
        "reviewer": "Brett",
    }


def _export_last(results: list[CallToolResult]) -> dict[str, Any]:
    return {"run_id": _ok(results[-1])["run_id"]}


@pytest.fixture
def database_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    url = _sqlite_url(tmp_path / "hosted.sqlite")
    monkeypatch.setenv(STORE_ENV, "database")
    monkeypatch.setenv(DATABASE_URL_ENV, url)
    yield url


def test_req_store_1_server_keeps_evidence_records_in_the_database(
    tmp_path: Path, database_env: str
) -> None:
    output_dir = tmp_path / "output"
    fetched, approved = _calls(output_dir, [FETCH, ("approve_evidence", _approve_first)])

    packet_id = _ok(fetched)["packet_id"]
    assert _ok(approved)["reviewer"] == "Brett"
    stored = DatabaseStore.from_url(database_env).get_packet(packet_id)
    assert stored is not None
    assert [item.observation.metric_id for item in stored.promotions] == [BROADBAND]
    assert not (output_dir / "research-workspace").exists()


def test_req_store_1_server_keeps_run_indexes_in_the_database(
    tmp_path: Path, database_env: str
) -> None:
    output_dir = tmp_path / "output"
    compared, exported = _calls(output_dir, [COMPARE, ("export_report", _export_last)])

    run_id = _ok(compared)["run_id"]
    assert _ok(exported)["reports"] == _ok(compared)["reports"]
    index = DatabaseStore.from_url(database_env).get_run_index(run_id)
    assert index is not None
    assert index.reports == _ok(compared)["reports"]
    assert not (output_dir / "runs").exists()


def test_req_store_1_server_uses_local_files_without_a_store_setting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(STORE_ENV, raising=False)
    fetched, approved, compared = _calls(
        tmp_path, [FETCH, ("approve_evidence", _approve_first), COMPARE]
    )

    _ok(approved)
    local = LocalStore(tmp_path)
    stored = local.get_packet(_ok(fetched)["packet_id"])
    assert stored is not None
    assert len(stored.promotions) == 1
    assert (tmp_path / "research-workspace" / f"{stored.packet.id}.json").is_file()
    assert (tmp_path / "runs" / f"{_ok(compared)['run_id']}.json").is_file()


# REQ-STORE-2 -------------------------------------------------------------------------------


def test_req_store_2_empty_store_has_no_records(store: Store) -> None:
    assert store.load_packets() == ()
    assert store.get_packet("0123456789ab") is None
    assert store.get_run_index("0123456789abcdef") is None


def test_req_store_2_packet_round_trips_unchanged(store: Store) -> None:
    saved = _packet("0123456789ab", "New Bern", "Lake Geneva")

    store.save_packet(saved)

    assert store.get_packet("0123456789ab") == saved
    assert store.load_packets() == (saved,)


def test_req_store_2_saving_a_packet_again_replaces_it(store: Store) -> None:
    first = _packet("0123456789ab")
    review = ReviewRecord(
        packet_id="0123456789ab",
        place_id="0000000",
        metric_id=BROADBAND,
        reviewer="Brett",
        decision=ReviewDecision.REJECTED,
        reason="value does not match the source table",
    )
    second = first.model_copy(update={"reviews": (review,)})

    store.save_packet(first)
    store.save_packet(second)

    assert store.load_packets() == (second,)


def test_req_store_2_packets_load_in_packet_id_order(store: Store) -> None:
    ids = ("ffffffffffff", "000000000000", "a0a0a0a0a0a0")
    for packet_id in ids:
        store.save_packet(_packet(packet_id))

    assert [item.packet.id for item in store.load_packets()] == sorted(ids)
    assert store.get_packet("a0a0a0a0a0a0") == _packet("a0a0a0a0a0a0")


def test_req_store_2_run_index_round_trips_and_replaces(store: Store) -> None:
    store.save_run_index(_index("0123456789abcdef", "a"))
    store.save_run_index(_index("fedcba9876543210", "b"))
    store.save_run_index(_index("0123456789abcdef", "c"))

    assert store.get_run_index("0123456789abcdef") == _index("0123456789abcdef", "c")
    assert store.get_run_index("fedcba9876543210") == _index("fedcba9876543210", "b")


@pytest.mark.parametrize("bad_id", ["", "../../etc/pw", "0123456789AB", "0123456789abc"])
def test_req_store_2_rejects_malformed_packet_ids(store: Store, bad_id: str) -> None:
    with pytest.raises(ValueError, match="packet ID"):
        store.get_packet(bad_id)
    with pytest.raises(ValueError, match="packet ID"):
        store.save_packet(_packet(bad_id))


@pytest.mark.parametrize("bad_id", ["", "../runs", "0123456789ABCDEF", "0123456789abcde"])
def test_req_store_2_rejects_malformed_run_ids(store: Store, bad_id: str) -> None:
    with pytest.raises(ValueError, match="run ID"):
        store.get_run_index(bad_id)
    with pytest.raises(ValueError, match="run ID"):
        store.save_run_index(_index(bad_id))


def _script(store: Store) -> list[object]:
    """One fixed sequence of writes and reads; both adapters must observe the same results."""
    seen: list[object] = [store.load_packets()]
    for packet_id in ("bbbbbbbbbbbb", "aaaaaaaaaaaa"):
        store.save_packet(_packet(packet_id, "New Bern"))
        seen.append(store.load_packets())
    store.save_packet(_packet("bbbbbbbbbbbb", "Lake Geneva", "Maryville"))
    store.save_run_index(_index("1111111111111111"))
    seen.extend(
        [
            store.load_packets(),
            store.get_packet("bbbbbbbbbbbb"),
            store.get_packet("cccccccccccc"),
            store.get_run_index("1111111111111111"),
            store.get_run_index("2222222222222222"),
        ]
    )
    return seen


def test_req_store_2_both_adapters_give_the_same_results(tmp_path: Path) -> None:
    local = _script(_build("local", tmp_path))
    database = _script(_build("database", tmp_path))

    assert local == database


def test_req_store_2_database_records_survive_a_new_connection(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path / "store.sqlite")
    DatabaseStore.from_url(url).save_packet(_packet("0123456789ab"))

    assert DatabaseStore.from_url(url).get_packet("0123456789ab") == _packet("0123456789ab")


def test_req_store_2_damaged_records_are_reported_not_skipped(tmp_path: Path) -> None:
    local = LocalStore(tmp_path / "local-output")
    local.save_packet(_packet("0123456789ab"))
    (tmp_path / "local-output" / "research-workspace" / "0123456789ab.json").write_text(
        "{not json", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="0123456789ab"):
        local.load_packets()

    url = _sqlite_url(tmp_path / "store.sqlite")
    database = DatabaseStore.from_url(url)
    database.save_packet(_packet("0123456789ab"))
    database.save_run_index(_index("0123456789abcdef"))
    with sqlite3.connect(tmp_path / "store.sqlite") as connection:
        connection.execute("UPDATE research_packets SET document = '{not json'")
        connection.execute("UPDATE run_report_indexes SET document = '{not json'")
    with pytest.raises(ValueError, match="0123456789ab"):
        database.load_packets()
    with pytest.raises(ValueError, match="0123456789ab"):
        database.get_packet("0123456789ab")
    with pytest.raises(ValueError, match="0123456789abcdef"):
        database.get_run_index("0123456789abcdef")


# REQ-STORE-3 -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "operation",
    [
        lambda store: store.load_packets(),
        lambda store: store.get_packet("0123456789ab"),
        lambda store: store.save_packet(_packet("0123456789ab")),
        lambda store: store.get_run_index("0123456789abcdef"),
        lambda store: store.save_run_index(_index("0123456789abcdef")),
    ],
)
def test_req_store_3_unreachable_database_raises_an_error_naming_the_store(
    tmp_path: Path, operation: Callable[[Store], object]
) -> None:
    store = DatabaseStore.from_url(_unreachable_url(tmp_path))

    with pytest.raises(StoreUnavailableError, match=r"database store .* is unreachable"):
        operation(store)


def test_req_store_3_store_errors_never_show_the_database_password(tmp_path: Path) -> None:
    url = "postgresql+psycopg://owner:s3cret-value@db.invalid:5432/lifescape"

    with pytest.raises(StoreError) as caught:
        DatabaseStore.from_url(url).load_packets()

    assert "database store" in str(caught.value)
    assert "s3cret-value" not in str(caught.value)


@pytest.fixture
def unreachable_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(STORE_ENV, "database")
    monkeypatch.setenv(DATABASE_URL_ENV, _unreachable_url(tmp_path))


@pytest.mark.usefixtures("unreachable_env")
def test_req_store_3_tool_calls_fail_naming_the_store_without_local_fallback(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "output"
    local = LocalStore(output_dir)
    seeded = _packet("0123456789ab")
    local.save_packet(seeded)
    local.save_run_index(_index("0123456789abcdef"))

    fetched, approved, exported = _calls(
        output_dir,
        [
            FETCH,
            (
                "approve_evidence",
                {
                    "packet_id": "0123456789ab",
                    "place_id": "0000000",
                    "metric_id": BROADBAND,
                    "reviewer": "Brett",
                },
            ),
            ("export_report", {"run_id": "0123456789abcdef"}),
        ],
    )

    for result in (fetched, approved, exported):
        assert result.is_error
        assert "database store" in _text(result)
        assert "is unreachable" in _text(result)
    assert local.load_packets() == (seeded,)


def test_req_store_3_database_refusals_name_the_store(tmp_path: Path) -> None:
    path = tmp_path / "store.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE research_packets (packet_id VARCHAR(12) PRIMARY KEY, "
            "document TEXT NOT NULL CHECK (length(document) < 10))"
        )
    store = DatabaseStore.from_url(_sqlite_url(path))

    with pytest.raises(StoreError, match=r"database store .* refused the request") as caught:
        store.save_packet(_packet("0123456789ab"))

    assert not isinstance(caught.value, StoreUnavailableError)
    assert store.load_packets() == ()


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://owner:s3cret-value@db.example:notaport/lifescape",
        "postgresql://owner:s3cret-value@[::1/lifescape",
        "owner:s3cret-value@db.example/lifescape",
    ],
)
def test_req_store_1_unparseable_database_url_names_the_store(tmp_path: Path, url: str) -> None:
    with pytest.raises(StoreConfigError, match="database store URL cannot be parsed") as caught:
        store_from_env(tmp_path, {STORE_ENV: "database", DATABASE_URL_ENV: url})

    assert "s3cret-value" not in str(caught.value)
