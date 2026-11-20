"""BUI-2009: the MCP agent surface (REQ-MCP-1..4) is traced in the PRD and the matrix.

``tests/test_traceability.py`` already proves every PRD requirement has a resolving matrix row.
These tests prove the MCP requirements are in the PRD at all, that each cites its ``REQ-MCP-n``
identifier, and that each matrix row points at the tests that carry that identifier, so a deleted
PRD requirement, a dropped row, or a renamed test fails here by name.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from test_traceability import REPOSITORY, matrix_rows, prd_requirements, references

MCP_REQUIREMENTS = {
    "FR17": "REQ-MCP-1",
    "FR18": "REQ-MCP-2",
    "FR19": "REQ-MCP-3",
    "FR20": "REQ-MCP-4",
}
MCP_TESTS = REPOSITORY / "tests/test_mcp_server.py"
ARCHITECTURE = REPOSITORY / "docs/architecture.md"


@pytest.mark.parametrize(("identifier", "req"), MCP_REQUIREMENTS.items())
def test_prd_states_each_mcp_requirement_with_its_req_identifier(identifier: str, req: str) -> None:
    requirements = prd_requirements()

    assert identifier in requirements
    assert f"({req})" in requirements[identifier]


@pytest.mark.parametrize(("identifier", "req"), MCP_REQUIREMENTS.items())
def test_matrix_row_cites_design_code_and_the_tests_for_the_requirement(
    identifier: str, req: str
) -> None:
    cells = matrix_rows()[identifier]
    design, code, unit, system = (references(cell) for cell in cells[2:6])
    slug = req.lower().replace("-", "_")

    assert design == ["docs/architecture.md::MCP agent surface"]
    assert all(reference.startswith("src/lifescape/mcp_server.py::") for reference in code)
    assert unit, identifier
    assert system, identifier
    for reference in unit:
        path, _, symbol = reference.partition("::")
        assert path == "tests/test_mcp_server.py"
        assert symbol.startswith(f"test_{slug}_"), f"{identifier}: {reference}"


def test_every_mcp_test_for_these_requirements_is_traced() -> None:
    declared = set(re.findall(r"(?m)^def (test_req_mcp_[1-4]_\w+)\(", MCP_TESTS.read_text("utf-8")))
    traced = {
        reference.partition("::")[2]
        for identifier in MCP_REQUIREMENTS
        for cell in matrix_rows()[identifier][2:6]
        for reference in references(cell)
        if reference.startswith("tests/")
    }

    assert declared
    assert traced <= declared
    # Each requirement traces at least one test carrying its own identifier.
    for req in MCP_REQUIREMENTS.values():
        slug = req.lower().replace("-", "_")
        assert any(name.startswith(f"test_{slug}_") for name in traced), req


def test_architecture_documents_the_mcp_surface_for_every_requirement() -> None:
    text = ARCHITECTURE.read_text("utf-8")
    section = text.split("## MCP agent surface")[1].split("\n## ")[0]

    assert "src/lifescape/mcp_server.py" in section
    for identifier in MCP_REQUIREMENTS:
        assert f"PRD {identifier}" in section


def test_mcp_requirements_leave_the_discovery_boundary_unchanged() -> None:
    requirements = prd_requirements()

    assert "not verified evidence" in requirements["FR18"]
    assert "cannot affect a gate or score" in requirements["FR18"]
    assert "never ranked" in requirements["FR19"]
    assert Path(MCP_TESTS).is_file()
