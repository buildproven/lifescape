from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from lifescape.discovery import (
    CatalogPlace,
    CatalogUnavailableError,
    DiscoveryCatalog,
    DiscoveryError,
    HardConstraint,
    PlaceDiscoveryService,
    SearchProfile,
    load_default_discovery_catalog,
    serialize_catalog,
)
from lifescape.models import ObservationRecord


def _place(
    place_id: str,
    name: str,
    state: str,
    *,
    population: float,
    housing_cost: float | None,
    population_density: float | None,
    car_light_commute_share: float | None,
    college_educated_share: float | None,
    older_adult_share: float | None,
) -> CatalogPlace:
    return CatalogPlace(
        place_id=place_id,
        name=name,
        state=state,
        values={
            "population": population,
            "housing_cost": housing_cost,
            "population_density": population_density,
            "car_light_commute_share": car_light_commute_share,
            "college_educated_share": college_educated_share,
            "older_adult_share": older_adult_share,
        },
    )


@pytest.fixture
def service() -> PlaceDiscoveryService:
    catalog = DiscoveryCatalog.from_places(
        (
            _place(
                "example",
                "Example",
                "MI",
                population=10_000,
                housing_cost=400_000,
                population_density=100,
                car_light_commute_share=30,
                college_educated_share=40,
                older_adult_share=20,
            ),
            _place(
                "alpha",
                "Alpha",
                "MI",
                population=8_000,
                housing_cost=410_000,
                population_density=105,
                car_light_commute_share=31,
                college_educated_share=39,
                older_adult_share=21,
            ),
            _place(
                "beta",
                "Beta",
                "WI",
                population=10_000,
                housing_cost=600_000,
                population_density=220,
                car_light_commute_share=12,
                college_educated_share=20,
                older_adult_share=10,
            ),
            _place(
                "unknown",
                "Unknown",
                "IL",
                population=6_000,
                housing_cost=None,
                population_density=150,
                car_light_commute_share=25,
                college_educated_share=35,
                older_adult_share=15,
            ),
            _place(
                "sparse",
                "Sparse",
                "OH",
                population=5_000,
                housing_cost=None,
                population_density=None,
                car_light_commute_share=None,
                college_educated_share=None,
                older_adult_share=None,
            ),
            _place(
                "small",
                "Small",
                "IN",
                population=2_499,
                housing_cost=200_000,
                population_density=40,
                car_light_commute_share=20,
                college_educated_share=20,
                older_adult_share=20,
            ),
        ),
        catalog_version="fixture-v1",
        data_date=date(2023, 12, 31),
    )
    return PlaceDiscoveryService(catalog)


def test_search_is_deterministic_and_explains_structured_matches(
    service: PlaceDiscoveryService,
) -> None:
    profile = SearchProfile(
        exemplar_place_ids=("example",),
        constraints=(
            HardConstraint(
                id="housing_cost_max",
                dimension="housing_cost",
                operator="max",
                value=500_000,
            ),
        ),
    )

    first = service.search(profile)
    second = service.search(profile)

    assert first.model_dump_json() == second.model_dump_json()
    assert [item.place_id for item in first.recommendations] == ["alpha", "unknown"]
    alpha = first.recommendations[0]
    assert alpha.component_count == 6
    assert len(alpha.reasons) >= 2
    assert alpha.catalog_version == "fixture-v1"
    assert first.diagnostics.serving_exemplar_count == 1
    assert first.diagnostics.excluded_any_constraint_count == 1
    assert first.diagnostics.insufficient_match_data_count == 1
    assert first.diagnostics.recommendable_with_unknown_constraints_count == 1
    assert all(not isinstance(item, ObservationRecord) for item in first.recommendations)


def test_missing_values_do_not_improve_score_and_remain_visible(
    service: PlaceDiscoveryService,
) -> None:
    result = service.search(SearchProfile(exemplar_place_ids=("example",)))
    unknown = next(item for item in result.recommendations if item.place_id == "unknown")

    assert "housing_cost" in unknown.missing_fields
    assert any("Needs verification" in tradeoff for tradeoff in unknown.tradeoffs)
    assert unknown.total_match < 1


def test_search_accepts_explicit_criteria_and_two_exemplars(
    service: PlaceDiscoveryService,
) -> None:
    explicit = service.search(
        SearchProfile(
            targets=(
                {"dimension": "population", "value": 8_000},
                {"dimension": "housing_cost", "value": 410_000},
            ),
            include_states=("MI",),
        )
    )
    two_exemplars = service.search(SearchProfile(exemplar_place_ids=("example", "alpha")))

    assert [item.place_id for item in explicit.recommendations] == ["alpha", "example"]
    assert all(item.place_id not in {"example", "alpha"} for item in two_exemplars.recommendations)
    assert len(two_exemplars.recommendations) <= 10


def test_non_eligible_exemplar_and_insufficient_profile_are_rejected(
    service: PlaceDiscoveryService,
) -> None:
    with pytest.raises(DiscoveryError, match="below the serving population"):
        service.search(SearchProfile(exemplar_place_ids=("small",)))
    with pytest.raises(DiscoveryError, match="at least two supported qualities"):
        service.search(SearchProfile(targets=({"dimension": "population", "value": 10_000},)))


def test_catalog_integrity_checks_hash_and_row_count(
    tmp_path: Path, service: PlaceDiscoveryService
) -> None:
    catalog_path = tmp_path / "catalog.json"
    manifest_path = tmp_path / "manifest.json"
    catalog_path.write_bytes(serialize_catalog(service.catalog.places))
    manifest_path.write_text(
        json.dumps(service.catalog.manifest.model_dump(mode="json")), encoding="utf-8"
    )

    loaded = DiscoveryCatalog.from_paths(catalog_path, manifest_path)
    assert loaded.manifest.output_sha256 == service.catalog.manifest.output_sha256

    catalog_path.write_bytes(catalog_path.read_bytes() + b" ")
    with pytest.raises(CatalogUnavailableError, match="hash mismatch"):
        DiscoveryCatalog.from_paths(catalog_path, manifest_path)


def test_shipped_catalog_has_verified_coverage() -> None:
    catalog = load_default_discovery_catalog()

    assert catalog.manifest.row_count == len(catalog.places)
    assert catalog.manifest.row_count >= 10_000
    assert all(
        catalog.manifest.coverage[dimension] >= 0.8 for dimension in catalog.manifest.coverage
    )
    assert catalog.manifest.source_hashes["gazetteer"]
    assert catalog.manifest.source_hashes["acs"]
