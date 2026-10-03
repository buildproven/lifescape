"""V-model traceability gate (PRD section 11).

Every PRD goal, functional requirement, non-functional requirement and acceptance criterion must
appear in ``docs/traceability.md`` with design, code and test references, and every reference must
resolve to a real file and symbol. A requirement edit, a deleted test, or a renamed function fails
this test, so the requirements, design, code and tests cannot drift apart silently.
"""

from __future__ import annotations

import re
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
PRD = REPOSITORY / "docs/prd/lifescape-place-discovery.md"
MATRIX = REPOSITORY / "docs/traceability.md"
PREFIX_WORDS = 7


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("**", "").replace("`", "")).strip()


def prd_requirements() -> dict[str, str]:
    """Return ``{id: normalized requirement text}`` for goals, FRs, NFRs and ACs."""
    text = PRD.read_text(encoding="utf-8")
    found: dict[str, str] = {}
    goals = text.split("## 2. Goals")[1].split("## 3.")[0]
    functional = text.split("## 5. Functional requirements")[1].split("## 6.")[0]
    for body in (goals, functional):
        for match in re.finditer(r"(?ms)^- ((?:G|FR)\d+[a-z]?): (.*?)(?=^- |\Z)", body):
            found[match.group(1)] = normalize(match.group(2))
    nonfunctional = text.split("## 6. Non-functional requirements")[1].split("## 7.")[0]
    for match in re.finditer(r"(?ms)^- ([A-Z][A-Za-z -]+): (.*?)(?=^- |\Z)", nonfunctional):
        found[f"NFR {match.group(1)}"] = normalize(match.group(2))
    criteria = text.split("## Acceptance criteria")[1]
    for match in re.finditer(r"(?ms)^- \[[ x]\] (AC\d+): (.*?)(?=^- \[|\Z)", criteria):
        found[match.group(1)] = normalize(match.group(2))
    return found


def matrix_rows() -> dict[str, list[str]]:
    rows: dict[str, list[str]] = {}
    for line in MATRIX.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| ") or line.startswith("| ID") or line.startswith("| -"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        rows[cells[0]] = cells
    return rows


def resolves(reference: str) -> str | None:
    """Return a problem description, or ``None`` when ``path[::symbol]`` resolves."""
    path_text, _, symbol = reference.partition("::")
    path = REPOSITORY / path_text
    if not path.is_file():
        return f"{reference}: file does not exist"
    if not symbol:
        return None
    content = path.read_text(encoding="utf-8")
    if path.suffix == ".md":
        return None if symbol.lower() in content.lower() else f"{reference}: heading not found"
    pattern = (
        rf"(?m)^\s*(?:async\s+)?(?:def|class|function|const|let)\s+{re.escape(symbol)}\b"
        rf"|^{re.escape(symbol)}\s*[:=]"
    )
    return None if re.search(pattern, content) else f"{reference}: symbol not found"


def references(cell: str) -> list[str]:
    return re.findall(r"`([^`]+)`", cell)


def test_matrix_covers_every_prd_requirement_exactly() -> None:
    requirements = prd_requirements()
    rows = matrix_rows()

    assert len(requirements) >= 45
    assert set(rows) == set(requirements), {
        "missing": sorted(set(requirements) - set(rows)),
        "unknown": sorted(set(rows) - set(requirements)),
    }


def test_matrix_quotes_the_current_prd_text() -> None:
    requirements = prd_requirements()
    for identifier, cells in matrix_rows().items():
        quoted = normalize(cells[1]).rstrip(".").strip()
        expected = " ".join(requirements[identifier].split()[:PREFIX_WORDS]).rstrip(".")
        assert quoted == expected, f"{identifier}: matrix text is stale; PRD now says {expected!r}"


def test_every_requirement_has_design_code_and_both_test_levels() -> None:
    problems: list[str] = []
    for identifier, cells in matrix_rows().items():
        design, code, unit, system = (references(cell) for cell in cells[2:6])
        if not system:
            problems.append(f"{identifier}: no system or acceptance test")
        if identifier.startswith("AC"):
            continue
        for label, refs in (("design", design), ("code", code), ("unit/integration", unit)):
            if not refs:
                problems.append(f"{identifier}: no {label} reference")
    assert not problems, "\n".join(problems)


def test_every_traceability_reference_resolves_to_a_real_file_and_symbol() -> None:
    problems = [
        problem
        for cells in matrix_rows().values()
        for cell in cells[2:6]
        for reference in references(cell)
        if (problem := resolves(reference))
    ]
    assert not problems, "\n".join(problems)


def test_test_references_name_tests() -> None:
    problems = []
    for identifier, cells in matrix_rows().items():
        for cell in cells[4:6]:
            for reference in references(cell):
                path, _, symbol = reference.partition("::")
                if not path.startswith("tests/") or not symbol.startswith("test_"):
                    problems.append(f"{identifier}: {reference} is not a pytest test")
    assert not problems, "\n".join(problems)


def test_documentation_test_references_resolve() -> None:
    problems = []
    for document in sorted((REPOSITORY / "docs").rglob("*.md")):
        for reference in re.findall(r"`(tests/[\w/]+\.py::test_\w+)`", document.read_text("utf-8")):
            if problem := resolves(reference):
                problems.append(f"{document.name}: {problem}")
    assert not problems, "\n".join(problems)
