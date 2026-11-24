"""BUI-2165: the NFR Privacy trace names what the hosted MCP stores.

The PRD's NFR Privacy text ("Search profiles and user decisions remain local") predates the
owner-only hosted MCP, which keeps research packets and run report indexes in the owner's
database. ``tests/test_traceability.py`` proves the row quotes the PRD and that every reference
resolves. These tests prove the row also traces the store that keeps data local by default and the
database store the hosted server uses, and that the matrix says so, so the row cannot claim again
that all data stays local.
"""

from __future__ import annotations

from test_traceability import MATRIX, matrix_rows, normalize, references

ROW = "NFR Privacy"
HOSTED_CODE = {"src/lifescape/store.py::store_from_env", "src/lifescape/store_db.py::DatabaseStore"}
STORE_TESTS = {
    "tests/test_store_contract.py::test_req_store_1_server_uses_local_files_without_a_store_setting",
    "tests/test_store_contract.py::test_req_store_1_server_keeps_evidence_records_in_the_database",
    "tests/test_store_contract.py::test_req_store_1_server_keeps_run_indexes_in_the_database",
}
PRIVACY_PAGE_TESTS = {
    "tests/test_hosted_deploy.py::test_req_deploy_5_privacy_policy_answers_200_on_the_hosted_site",
    "tests/test_hosted_deploy.py::test_req_deploy_5_local_app_has_no_hosted_privacy_page",
}


def _row() -> list[str]:
    rows = matrix_rows()
    assert ROW in rows, f"{ROW} row is missing from docs/traceability.md"
    return rows[ROW]


def _field_note() -> str:
    notes = MATRIX.read_text(encoding="utf-8").split("## Field notes", 1)
    assert len(notes) == 2, "docs/traceability.md has no Field notes section"
    for entry in notes[1].split("\n- "):
        if entry.lstrip("- ").startswith(f"{ROW} (BUI-2165)"):
            return normalize(entry)
    raise AssertionError(f"no {ROW} (BUI-2165) field note")


def test_privacy_row_keeps_the_local_discovery_trace() -> None:
    design, code, unit, system = (set(references(cell)) for cell in _row()[2:6])

    assert "docs/decisions/ADR-place-discovery-contract.md::Migration and rollback" in design
    assert {
        "src/lifescape/web.py::recommend_places",
        "src/lifescape/static/scenario.js::save",
    } <= code
    assert (
        "tests/test_web.py::"
        "test_discovery_recommendations_return_the_documented_body_without_running_evidence"
    ) in unit
    assert (
        "tests/test_user_journey.py::test_discovery_journey_from_example_town_to_recovered_shortlist"
    ) in system


def test_privacy_row_traces_the_hosted_database_store() -> None:
    design, code, unit, system = (set(references(cell)) for cell in _row()[2:6])

    assert "docs/api-inventory.md::Hosted site routes" in design
    assert HOSTED_CODE.issubset(code), sorted(HOSTED_CODE - code)
    assert STORE_TESTS.issubset(unit), sorted(STORE_TESTS - unit)
    assert PRIVACY_PAGE_TESTS.issubset(system), sorted(PRIVACY_PAGE_TESTS - system)


def test_privacy_field_note_states_what_the_hosted_server_stores() -> None:
    note = _field_note()

    for phrase in (
        "hosted MCP",
        "LIFESCAPE_STORE=database",
        "research packets",
        "run report indexes",
        "owner's database",
        "src/lifescape/templates/privacy.html",
    ):
        assert phrase in note, f"field note does not mention {phrase!r}"
    assert "does not hold" in note


def test_privacy_page_lists_the_tables_the_field_note_names() -> None:
    page = (MATRIX.parents[1] / "src/lifescape/templates/privacy.html").read_text(encoding="utf-8")

    for table in ("research_packets", "run_report_indexes"):
        assert table in page
    assert "owner's own database" in page
