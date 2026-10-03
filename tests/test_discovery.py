"""Discovery module tests (PRD AC2, AC6; ADR-place-discovery-contract).

The fixture catalog is synthetic and hand-computed: bounds are 0-100 for every field except
population (0-10,000) and median home value (0-1,000), so each normalized value is raw/bound
and every expected total below is worked by hand rather than copied from the code under test.
"""

from __future__ import annotations

import ast
import csv
import gzip
import hashlib
import io
import json
import time
from pathlib import Path

import pytest
from pydantic import ValidationError

import lifescape.discovery as discovery_module
from lifescape.discovery import (
    CATALOG_COLUMNS,
    SCORED_FIELDS,
    STATE_REGIONS,
    CatalogUnavailableError,
    DiscoveryService,
    HardConstraint,
    PlaceCatalog,
    ProfileError,
    SearchProfile,
    format_value,
    load_catalog,
    nearest_rank_percentile,
    parse_catalog,
)
from lifescape.models import ObservationRecord

FIELD_META = {
    field: {"label": field.replace("_", " ").title(), "unit": "percent", "definition": field}
    for field in SCORED_FIELDS
}
FIELD_META["population"]["unit"] = "people"
FIELD_META["median_home_value"]["unit"] = "USD"
BOUNDS = {field: {"lower": 0.0, "upper": 100.0} for field in SCORED_FIELDS}
BOUNDS["population"]["upper"] = 10000.0
BOUNDS["median_home_value"]["upper"] = 1000.0

# id: (name, state, pop, home value, density, car-light, college, older)
ROWS: dict[str, tuple[str, str, float | None, ...]] = {
    "EX": ("Example", "NC", 5000, 500, 50, 20, 40, 30),
    "A": ("Alpha", "NC", 5000, 500, 50, 20, 40, 30),
    "B": ("Bravo", "MI", 7500, 400, 60, 30, 50, 20),
    "C": ("Charlie", "OR", 5000, None, None, 20, 40, 30),
    "D": ("Delta", "OR", 5000, None, None, None, None, 30),
    "E": ("Echo", "TX", 5000, None, None, None, None, None),
    "F": ("Foxtrot", "TX", 1000, 500, 50, 20, 40, 30),
    "G": ("Golf", "TX", None, 500, 50, 20, 40, 30),
    "H": ("Hotel", "MI", 7500, 900, 60, 30, 50, 20),
    "Y": ("Yankee", "ME", 5000, 500, 50, 20, 90, 80),
    "Z": ("Zulu", "ME", 5000, 500, 50, 20, 40, None),
}


def build_catalog(
    rows: dict[str, tuple[str, str, float | None, ...]] | None = None,
) -> PlaceCatalog:
    rows = ROWS if rows is None else rows
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CATALOG_COLUMNS)
    for place_id, (name, state, *values) in rows.items():
        writer.writerow(
            [place_id, name, state, STATE_REGIONS[state]]
            + ["" if value is None else value for value in values]
            + ["1.0", "0", "0"]
        )
    compressed = gzip.compress(buffer.getvalue().encode(), mtime=0)
    manifest = {
        "catalog_version": "synthetic-fixture-v1",
        "data_date": "2000-01-01",
        "fields": FIELD_META,
        "bounds": BOUNDS,
        "row_count": len(rows),
        "output_sha256": hashlib.sha256(compressed).hexdigest(),
    }
    return parse_catalog(compressed, manifest)


@pytest.fixture(scope="module")
def service() -> DiscoveryService:
    return DiscoveryService(build_catalog())


def ids(result: dict) -> list[str]:
    return [item["place_id"] for item in result["recommendations"]]


def test_fixture_search_matches_hand_computed_order_and_totals(service: DiscoveryService) -> None:
    result = service.search(SearchProfile(exemplars=("EX",)))

    # A identical: 6/6. B: (0.75 + 5 * 0.9) / 6 = 0.875. Y and Z both total 5/6, Y with six
    # components to Z's five. H: (0.75 + 0.6 + 4 * 0.9) / 6 = 0.825. C: 4/6, its two missing
    # fields still in the denominator. D: 2/6. E has one component and is not recommended.
    assert ids(result) == ["A", "B", "Y", "Z", "H", "C", "D"]
    totals = {item["place_id"]: item["total_match"] for item in result["recommendations"]}
    assert totals["A"] == 1.0
    assert totals["B"] == 0.875
    assert totals["Y"] == totals["Z"] == 0.833333
    assert totals["H"] == 0.825
    assert totals["C"] == 0.666667
    assert totals["D"] == 0.333333
    assert "E" not in totals


def test_equal_totals_rank_more_components_first_before_place_id() -> None:
    rows = {
        "EX": ROWS["EX"],
        "A_FEWER": ("Zulu Fewer", "ME", 5000, 500, 50, 20, 40, None),  # 5 comps, 5/6
        "B_MORE": ("Yankee More", "ME", 5000, 500, 50, 20, 90, 80),  # 6 comps, 5/6
    }
    result = DiscoveryService(build_catalog(rows)).search(SearchProfile(exemplars=("EX",)))

    assert ids(result) == ["B_MORE", "A_FEWER"]
    assert [item["component_count"] for item in result["recommendations"]] == [6, 5]


def test_missing_values_never_raise_a_score_and_keep_their_weight(
    service: DiscoveryService,
) -> None:
    result = service.search(SearchProfile(exemplars=("EX",)))
    charlie = next(item for item in result["recommendations"] if item["place_id"] == "C")

    assert charlie["component_count"] == 4
    assert charlie["profile_target_count"] == 6
    assert charlie["missing_fields"] == ["median_home_value", "population_density"]
    # Sum of weight * similarity (4 * 3) over the full weight (6 * 3): 0.666667, not 1.0.
    assert charlie["total_match"] == 0.666667
    assert sum(c["score_contribution"] for c in charlie["components"]) == pytest.approx(
        charlie["total_match"], abs=1e-5
    )


def test_candidates_with_fewer_than_two_components_are_counted_not_recommended(
    service: DiscoveryService,
) -> None:
    result = service.search(SearchProfile(exemplars=("EX",)))

    assert "E" not in ids(result)
    assert result["diagnostics"]["insufficient_match_data_count"] == 1


def test_exemplar_is_never_recommended_and_catalog_partition_holds(
    service: DiscoveryService,
) -> None:
    result = service.search(SearchProfile(exemplars=("EX",)))
    diagnostics = result["diagnostics"]

    assert "EX" not in ids(result)
    assert diagnostics["catalog_places"] == len(ROWS)
    assert diagnostics["missing_population_count"] == 1  # G
    assert diagnostics["below_minimum_population_count"] == 1  # F
    assert diagnostics["serving_places"] == 9
    assert diagnostics["catalog_places"] == (
        diagnostics["missing_population_count"]
        + diagnostics["below_minimum_population_count"]
        + diagnostics["serving_places"]
    )
    assert (
        diagnostics["serving_places"]
        - diagnostics["serving_exemplar_count"]
        - diagnostics["user_excluded_count"]
        - diagnostics["excluded_any_constraint_count"]
        - diagnostics["insufficient_match_data_count"]
        == diagnostics["recommendable_count"]
    )
    assert diagnostics["returned_count"] == min(10, diagnostics["recommendable_count"])


def test_known_failing_hard_constraint_excludes_and_unknown_stays_visible(
    service: DiscoveryService,
) -> None:
    profile = SearchProfile(
        exemplars=("EX",),
        hard_constraints=(HardConstraint(field="median_home_value", operator="max", value=800),),
    )
    result = service.search(profile)

    assert "H" not in ids(result)  # known value 900 fails the 800 cap
    charlie = next(item for item in result["recommendations"] if item["place_id"] == "C")
    assert charlie["unknown_constraints"] == ["median_home_value_max"]
    assert result["diagnostics"]["known_constraint_exclusions"] == [
        {"constraint_id": "median_home_value_max", "excluded_count": 1, "unknown_count": 2}
    ]  # unknown: C and D (E also lacks a value but is not recommendable)
    assert result["diagnostics"]["excluded_any_constraint_count"] == 1
    assert result["diagnostics"]["recommendable_with_unknown_constraints_count"] == 2


def test_state_and_region_filters_are_constraints_not_score_components(
    service: DiscoveryService,
) -> None:
    profile = SearchProfile(exemplars=("EX",), include_states=("ME",))
    result = service.search(profile)

    assert set(ids(result)) == {"Y", "Z"}
    assert result["diagnostics"]["known_constraint_exclusions"][0]["constraint_id"] == (
        "state_include"
    )
    for item in result["recommendations"]:
        assert all(c["field"] in SCORED_FIELDS for c in item["components"])
        assert item["profile_target_count"] == 6


def test_explicit_target_overrides_exemplar_for_that_dimension(service: DiscoveryService) -> None:
    profile = SearchProfile(exemplars=("EX",), targets={"college_educated_share": 90})
    result = service.search(profile)
    yankee = next(item for item in result["recommendations"] if item["place_id"] == "Y")
    college = next(c for c in yankee["components"] if c["field"] == "college_educated_share")

    assert college["target_source"] == "user"
    assert college["target_value"] == 90
    assert college["similarity"] == 1.0
    assert college["matched_exemplar"] is None


def test_two_exemplars_use_best_similarity_once_and_record_all_targets() -> None:
    rows = {
        "X1": ("One", "NC", 2500, 500, 50, 20, 40, 30),
        "X2": ("Two", "NC", 7500, 500, 50, 20, 40, 30),
        "NEAR2": ("Near Two", "MI", 7000, 500, 50, 20, 40, 30),
    }
    result = DiscoveryService(build_catalog(rows)).search(SearchProfile(exemplars=("X1", "X2")))
    near = result["recommendations"][0]
    population = next(c for c in near["components"] if c["field"] == "population")

    assert near["place_id"] == "NEAR2"
    assert len([c for c in near["components"] if c["field"] == "population"]) == 1
    assert population["matched_exemplar"]["place_id"] == "X2"
    assert {t["place_id"] for t in population["available_targets"]} == {"X1", "X2"}
    assert population["similarity"] == pytest.approx(0.95)


def test_priorities_change_the_denominator() -> None:
    rows = {"EX": ROWS["EX"], "Y": ROWS["Y"]}
    result = DiscoveryService(build_catalog(rows)).search(
        SearchProfile(exemplars=("EX",), priorities={"college_educated_share": 5})
    )
    yankee = result["recommendations"][0]

    # college similarity 0.5 (weight 5); five others 1.0 and 0.5 (older), weight 3 each:
    # (5 * 0.5 + 3 * (1 + 1 + 1 + 1 + 0.5)) / (5 + 5 * 3) = 16 / 20 = 0.8.
    assert yankee["total_match"] == 0.8


def test_clipping_is_recorded_and_not_called_an_exact_match() -> None:
    rows = {
        "EX": ("Example", "NC", 5000, 500, 50, 20, 150, 30),
        "J": ("Juliet", "MI", 5000, 500, 50, 20, 130, 30),
    }
    result = DiscoveryService(build_catalog(rows)).search(SearchProfile(exemplars=("EX",)))
    college = next(
        c
        for c in result["recommendations"][0]["components"]
        if c["field"] == "college_educated_share"
    )

    assert college["candidate_clipped"] and college["target_clipped"]
    assert college["similarity"] == 1.0
    assert college["target_value"] == 150 and college["candidate_value"] == 130
    assert college["upper_bound"] == 100


def test_every_recommendation_has_two_reasons_a_difference_and_traceable_prose(
    service: DiscoveryService,
) -> None:
    result = service.search(SearchProfile(exemplars=("EX",)))
    for item in result["recommendations"]:
        assert len(item["reasons"]) >= 2
        assert item["differences"]
        by_field = {c["field"]: c for c in item["components"]}
        for entry in (*item["reasons"], *item["differences"]):
            component = by_field[entry["field"]]
            assert format_value(component["unit"], component["candidate_value"]) in entry["text"]
            assert component["label"] in entry["text"]


def test_field_details_label_every_value_as_discovery_data_or_missing(
    service: DiscoveryService,
) -> None:
    result = service.search(SearchProfile(exemplars=("EX",)))
    charlie = next(item for item in result["recommendations"] if item["place_id"] == "C")
    states = {f["field"]: f["status"] for f in charlie["fields"]}

    assert states["median_home_value"] == "missing"
    assert states["population"] == "discovery data"
    assert all(f["evidence_status"] == "not verified evidence" for f in charlie["fields"])


def test_repeat_search_is_byte_identical(service: DiscoveryService) -> None:
    profile = SearchProfile(exemplars=("EX",), exclude_regions=("West",))
    first = json.dumps(service.search(profile), sort_keys=True)
    second = json.dumps(service.search(profile), sort_keys=True)

    assert first == second


def test_limit_caps_results_and_ranks_are_sequential(service: DiscoveryService) -> None:
    result = service.search(SearchProfile(exemplars=("EX",)), limit=2)

    assert [item["rank"] for item in result["recommendations"]] == [1, 2]
    assert result["diagnostics"]["returned_count"] == 2
    assert result["diagnostics"]["recommendable_count"] > 2


@pytest.mark.parametrize(
    ("profile", "message"),
    [
        (SearchProfile(exemplars=("F",)), "cannot be an example"),
        (SearchProfile(exemplars=("G",)), "cannot be an example"),
        (SearchProfile(exemplars=("missing",)), "unknown exemplar"),
        (SearchProfile(exemplars=("E",)), "at least two supported qualities"),
        (SearchProfile(targets={"population": 5000}), "at least two supported qualities"),
    ],
)
def test_profiles_that_cannot_search_are_rejected(
    service: DiscoveryService, profile: SearchProfile, message: str
) -> None:
    with pytest.raises(ProfileError, match=message):
        service.search(profile)


def test_priority_without_a_target_is_rejected(service: DiscoveryService) -> None:
    profile = SearchProfile(
        targets={"population": 5000, "median_home_value": 500},
        priorities={"older_adult_share": 4},
    )
    with pytest.raises(ProfileError, match="nothing targets"):
        service.search(profile)


def test_two_supported_qualities_alone_can_search(service: DiscoveryService) -> None:
    result = service.search(SearchProfile(targets={"population": 5000, "older_adult_share": 30}))

    assert result["recommendations"]
    assert all(item["profile_target_count"] == 2 for item in result["recommendations"])


@pytest.mark.parametrize(
    "payload",
    [
        {"exemplars": ["A", "B", "C"]},
        {"exemplars": ["A", "A"]},
        {"priorities": {"population": 6}},
        {"priorities": {"population": 0}},
        {"targets": {"population": -1}},
        {"targets": {"unknown_field": 1}},
        {"include_states": ["ZZ"]},
        {"include_states": ["NC"], "exclude_states": ["NC"]},
        {"include_regions": ["Mars"]},
        {"hard_constraints": [{"field": "population", "operator": "max", "value": -5}]},
        {
            "hard_constraints": [
                {"field": "population", "operator": "max", "value": 5},
                {"field": "population", "operator": "max", "value": 6},
            ]
        },
        {"surprise": 1},
    ],
)
def test_invalid_profiles_fail_validation(payload: dict) -> None:
    with pytest.raises(ValidationError):
        SearchProfile.model_validate(payload)


def test_lookup_is_accent_and_case_insensitive_and_ranks_exact_first() -> None:
    rows = {
        "1": ("San José", "CA", 9000, 1, 1, 1, 1, 1),
        "2": ("San Jose Heights", "CA", 99999, 1, 1, 1, 1, 1),
        "3": ("Saint San Jose", "OR", 5000, 1, 1, 1, 1, 1),
    }
    catalog = build_catalog(rows)

    assert [p.place_id for p in catalog.lookup("san jose")] == ["1", "2", "3"]
    assert [p.place_id for p in catalog.lookup("SAN JOSÉ, ca")] == ["1", "2"]
    assert [p.place_id for p in catalog.lookup("san jose, or")] == ["3"]
    assert catalog.lookup("zzz") == []


@pytest.mark.parametrize(
    ("unit", "value", "expected"),
    [
        ("USD", 310000.4, "$310,000"),
        ("percent of workers", 12.34, "12.3%"),
        ("people", 12345, "12,345"),
        ("people per square mile", 1884.2, "1,884 people per sq mi"),
    ],
)
def test_format_value(unit: str, value: float, expected: str) -> None:
    assert format_value(unit, value) == expected


def test_nearest_rank_percentile_rule() -> None:
    values = [float(n) for n in range(1, 21)]

    assert nearest_rank_percentile(values, 0.05) == 1.0  # ceil(1.0) - 1 = index 0
    assert nearest_rank_percentile(values, 0.95) == 19.0  # ceil(19.0) - 1 = index 18
    assert nearest_rank_percentile([7.0], 0.95) == 7.0
    with pytest.raises(ValueError):
        nearest_rank_percentile([], 0.5)


# -- catalog integrity (PRD risk: damaged catalog) ---------------------------------------


def _packaged() -> tuple[bytes, dict]:
    package = Path(discovery_module.__file__).parent / "data"
    manifest = json.loads((package / "place-catalog.manifest.json").read_text("utf-8"))
    return (package / manifest["output_file"]).read_bytes(), manifest


def test_integrity_failures_never_load_partial_rows() -> None:
    compressed, manifest = _packaged()

    with pytest.raises(CatalogUnavailableError, match="hash"):
        parse_catalog(compressed[:-10] + b"0123456789", manifest)
    with pytest.raises(CatalogUnavailableError, match="row count"):
        parse_catalog(compressed, {**manifest, "row_count": manifest["row_count"] + 1})
    truncated = compressed[: len(compressed) // 2]
    with pytest.raises(CatalogUnavailableError):
        parse_catalog(
            truncated, {**manifest, "output_sha256": hashlib.sha256(truncated).hexdigest()}
        )


def test_negative_missing_value_sentinel_is_rejected() -> None:
    row = ["1", "Bad", "NC", "South", "-666666666", "", "", "", "", "", "1", "0", "0"]
    text = ",".join(CATALOG_COLUMNS) + "\n" + ",".join(row) + "\n"
    compressed = gzip.compress(text.encode(), mtime=0)
    manifest = {
        "catalog_version": "x",
        "data_date": "x",
        "fields": FIELD_META,
        "bounds": BOUNDS,
        "row_count": 1,
        "output_sha256": hashlib.sha256(compressed).hexdigest(),
    }

    with pytest.raises(CatalogUnavailableError, match="nonnegative"):
        parse_catalog(compressed, manifest)


def test_duplicate_ids_and_wrong_columns_are_rejected() -> None:
    header = ",".join(CATALOG_COLUMNS)
    row = "1,Dup,NC,South,5000,1,1,1,1,1,1,0,0"
    for text, message in (
        (f"{header}\n{row}\n{row}\n", "duplicate"),
        ("wrong,columns\n1,2\n", "columns"),
    ):
        compressed = gzip.compress(text.encode(), mtime=0)
        manifest = {
            "catalog_version": "x",
            "data_date": "x",
            "fields": FIELD_META,
            "bounds": BOUNDS,
            "row_count": 2,
            "output_sha256": hashlib.sha256(compressed).hexdigest(),
        }
        with pytest.raises(CatalogUnavailableError, match=message):
            parse_catalog(compressed, manifest)


def test_unreadable_catalog_files_report_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    class Missing:
        def joinpath(self, *_: str) -> Missing:
            return self

        def read_text(self, *_: str) -> str:
            raise FileNotFoundError("gone")

    monkeypatch.setattr(discovery_module.resources, "files", lambda _: Missing())

    with pytest.raises(CatalogUnavailableError, match="unreadable"):
        load_catalog()


# -- the shipped catalog (PRD FR4, FR5, AC2) ---------------------------------------------


@pytest.fixture(scope="module")
def real() -> PlaceCatalog:
    return load_catalog()


def test_shipped_catalog_meets_the_fr4_coverage_floor(real: PlaceCatalog) -> None:
    serving = real.serving_places

    assert len(serving) > 5000
    for field in SCORED_FIELDS:
        known = sum(1 for place in serving if place.values[field] is not None)
        assert known / len(serving) >= 0.8, field
    assert all(
        value is None or value >= 0
        for place in real.places.values()
        for value in place.values.values()
    )


def test_shipped_manifest_bounds_match_the_nearest_rank_rule(real: PlaceCatalog) -> None:
    for field in SCORED_FIELDS:
        values = sorted(
            place.values[field] for place in real.serving_places if place.values[field] is not None
        )
        assert real.bounds[field]["lower"] == nearest_rank_percentile(values, 0.05)
        assert real.bounds[field]["upper"] == nearest_rank_percentile(values, 0.95)


def test_shipped_manifest_records_provenance_and_unsupported_qualities(
    real: PlaceCatalog,
) -> None:
    manifest = real.manifest

    assert set(manifest["inputs"]) == {
        "gazetteer",
        "b01003",
        "b25077",
        "b08301",
        "b15003",
        "b01001",
    }
    assert all(len(item["sha256"]) == 64 for item in manifest["inputs"].values())
    assert "-666666666" in manifest["sentinel_policy"]
    assert "not a walkability score" in manifest["fields"]["car_light_commute_share"]["definition"]
    assert set(manifest["fields"]) == set(SCORED_FIELDS)


def test_real_search_is_deterministic_serving_only_and_fast(real: PlaceCatalog) -> None:
    service = DiscoveryService(real)
    exemplar = real.lookup("Traverse City, MI")[0]
    profile = SearchProfile(exemplars=(exemplar.place_id,), exclude_regions=("South",))

    started = time.perf_counter()
    first = service.search(profile)
    elapsed = time.perf_counter() - started
    second = service.search(profile)

    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert elapsed < 2.0
    assert len(first["recommendations"]) == 10
    assert exemplar.place_id not in ids(first)
    assert all(real.places[item["place_id"]].serving_eligible for item in first["recommendations"])
    assert all(real.places[item["place_id"]].region != "South" for item in first["recommendations"])


def test_small_towns_are_lookup_only_not_exemplars(real: PlaceCatalog) -> None:
    small = next(p for p in real.places.values() if p.population and p.population < 2500)
    service = DiscoveryService(real)

    assert real.lookup(small.name, 5)
    with pytest.raises(ProfileError, match="cannot be an example"):
        service.search(SearchProfile(exemplars=(small.place_id,)))


# -- discovery/evidence authority boundary (PRD FR14, G5) ---------------------------------

FORBIDDEN_IMPORTS = {"pipeline", "evidence", "gates", "scoring", "sensitivity", "db", "reports"}


def test_discovery_module_imports_no_evidence_gate_or_scoring_seam() -> None:
    tree = ast.parse(Path(discovery_module.__file__).read_text("utf-8"))
    imported = {
        alias.name.split(".")[-1]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.split(".")[-1]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("lifescape")
    }

    assert not imported & FORBIDDEN_IMPORTS


def test_a_discovery_record_cannot_become_an_observation(service: DiscoveryService) -> None:
    result = service.search(SearchProfile(exemplars=("EX",)))
    component = result["recommendations"][0]["components"][0]

    with pytest.raises(ValidationError):
        ObservationRecord.model_validate(component)
    with pytest.raises(ValidationError):
        ObservationRecord.model_validate(
            {"place": {"place_id": "A", "name": "Alpha", "state": "NC"}, **component}
        )


def test_user_excluded_places_are_removed_and_counted(service: DiscoveryService) -> None:
    result = service.search(SearchProfile(exemplars=("EX",), exclude_places=("A", "B")))
    diagnostics = result["diagnostics"]

    assert ids(result) == ["Y", "Z", "H", "C", "D"]
    assert diagnostics["user_excluded_count"] == 2
    assert (
        diagnostics["serving_places"]
        - diagnostics["serving_exemplar_count"]
        - diagnostics["user_excluded_count"]
        - diagnostics["excluded_any_constraint_count"]
        - diagnostics["insufficient_match_data_count"]
        == diagnostics["recommendable_count"]
    )
    assert result["profile"]["exclude_places"] == ["A", "B"]


def test_unknown_or_example_excluded_places_are_rejected(service: DiscoveryService) -> None:
    with pytest.raises(ProfileError, match="unknown excluded town"):
        service.search(SearchProfile(exemplars=("EX",), exclude_places=("nope",)))
    with pytest.raises(ValidationError):
        SearchProfile(exemplars=("EX",), exclude_places=("EX",))
    with pytest.raises(ValidationError):
        SearchProfile(exclude_places=("A", "A"))


def test_release_budget_catalog_load_and_p95_search_latency() -> None:
    """PRD Performance: load is a 3 s release target; p95 search is at most 2 s."""
    started = time.perf_counter()
    catalog = load_catalog()
    load_seconds = time.perf_counter() - started
    service = DiscoveryService(catalog)
    exemplars = [
        place.place_id for place in catalog.serving_places[:: len(catalog.serving_places) // 20]
    ]

    timings = []
    for place_id in exemplars:
        begin = time.perf_counter()
        service.search(SearchProfile(exemplars=(place_id,)))
        timings.append(time.perf_counter() - begin)
    timings.sort()

    assert load_seconds <= 3.0
    assert timings[int(len(timings) * 0.95) - 1] <= 2.0
