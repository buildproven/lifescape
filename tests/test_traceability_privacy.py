"""BUI-2165: the traceability matrix NFR Privacy row covers the hosted store.

REQ-PRIV-1: the NFR Privacy row traces the hosted store (``DatabaseStore``), the hosted
``create_server`` and the privacy page, not only the local app. REQ-PRIV-2: the row cites tests
for the hosted per-user read and delete, and the privacy page; every reference still resolves.
The row's quoted PRD text stays the PRD's own words. Behavior is unchanged: this is a docs check.
"""

from __future__ import annotations

from pathlib import Path

from test_traceability import MATRIX, matrix_rows, references, resolves

REPOSITORY = Path(__file__).resolve().parents[1]
ROW = "NFR Privacy"


def _cells() -> list[str]:
    return matrix_rows()[ROW]


def _refs(column: int) -> list[str]:
    return references(_cells()[column])


def test_req_priv_1_row_keeps_the_local_references() -> None:
    assert "src/lifescape/static/scenario.js::save" in _refs(3)
    assert "src/lifescape/web.py::recommend_places" in _refs(3)
    assert any(ref.startswith("docs/decisions/ADR-place-discovery-contract.md") for ref in _refs(2))


def test_req_priv_1_row_traces_the_hosted_store_in_code() -> None:
    code = _refs(3)
    assert "src/lifescape/store_db.py::DatabaseStore" in code
    assert "src/lifescape/mcp_server.py::create_server" in code
    assert "src/lifescape/templates/privacy.html" in code


def test_req_priv_1_row_traces_the_hosted_design() -> None:
    assert "docs/architecture.md::MCP agent surface" in _refs(2)


def test_req_priv_2_row_cites_hosted_tests_at_both_levels() -> None:
    unit = _refs(4)
    system = _refs(5)
    assert any(ref.startswith("tests/test_store_per_user.py::") for ref in unit)
    assert any(ref.startswith("tests/test_delete_my_data.py::") for ref in unit)
    assert any(ref.startswith("tests/test_delete_my_data.py::test_req_del_2_") for ref in system)
    assert system, "no system or acceptance test"


def test_req_priv_2_every_reference_in_the_row_resolves() -> None:
    problems = [
        problem
        for column in range(2, 6)
        for reference in _refs(column)
        if (problem := resolves(reference))
    ]
    assert not problems, "\n".join(problems)


def test_req_priv_2_the_hosted_store_is_described_in_the_field_notes() -> None:
    notes = MATRIX.read_text(encoding="utf-8").split("## Field notes")[1]
    paragraph = notes.split("- NFR Privacy, hosted")[1].split("\n- ")[0]
    assert "research packets and run report indexes" in " ".join(paragraph.split())
    assert "delete_my_data" in paragraph
    assert "tests/test_traceability_privacy.py" in paragraph


def test_the_privacy_row_still_quotes_the_prd() -> None:
    assert _cells()[1] == "Search profiles and user decisions remain local."
