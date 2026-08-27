from pathlib import Path

PRD_PATH = "docs/prd/lifescape-place-discovery.md"


def test_repository_surfaces_reference_the_approved_product_contract() -> None:
    repository = Path(__file__).resolve().parents[1]
    prd = (repository / PRD_PATH).read_text(encoding="utf-8")
    readme = (repository / "README.md").read_text(encoding="utf-8")
    implementation = (repository / "docs/implementation-plan.md").read_text(encoding="utf-8")
    instructions = (repository / "CLAUDE.md").read_text(encoding="utf-8")
    pull_request_template = (repository / ".github/pull_request_template.md").read_text(
        encoding="utf-8"
    )
    discovery_adr = (repository / "docs/decisions/ADR-place-discovery-contract.md").read_text(
        encoding="utf-8"
    )
    old_boundary = (repository / "docs/decisions/ADR-v1-product-boundary.md").read_text(
        encoding="utf-8"
    )

    assert "> Status: APPROVED" in prd
    assert "preferences and examples discover" in readme
    assert "place-discovery PRD" in readme
    assert PRD_PATH in implementation
    assert PRD_PATH in instructions
    assert "PRD trace" in pull_request_template
    assert "User-visible vertical behavior" in pull_request_template
    assert PRD_PATH in discovery_adr
    assert "Superseded by `docs/prd/lifescape-place-discovery.md`" in old_boundary


def test_product_contract_prevents_infrastructure_only_scope() -> None:
    repository = Path(__file__).resolve().parents[1]
    prd = (repository / PRD_PATH).read_text(encoding="utf-8")
    tasks = (repository / "docs/prd/lifescape-place-discovery-tasks.md").read_text(encoding="utf-8")

    assert "Work that cannot cite an approved requirement is out of scope." in prd
    assert "cannot be roadmap goals by themselves" in prd
    assert "user-visible vertical slice" in prd
    assert "Delivers:" in tasks
    assert "Verification:" in tasks
