import csv
import json
from pathlib import Path

from lifescape.config import load_metrics, load_sources
from lifescape.evidence_audit import audit_manual_evidence, write_evidence_audit

CONFIG_DIR = Path(__file__).parents[1] / "config"


def _write_wide_evidence(path: Path, values: dict[str, str]) -> None:
    columns = [
        "place_id",
        "place_name",
        "state",
        "geography_type",
        "source_url",
        "source_title",
        "publisher",
        "tier",
        "retrieved_at",
        "observed_period",
        "observed_at",
        "source_geography",
        "confidence",
        "synthetic",
        *values,
    ]
    row = {
        "place_id": "town",
        "place_name": "Town",
        "state": "NC",
        "geography_type": "town",
        "source_url": "https://www.zillow.com/home-values/town",
        "source_title": "Town Home Values (ZHVI)",
        "publisher": "Zillow",
        "tier": "B",
        "retrieved_at": "2026-01-01",
        "observed_period": "2025",
        "observed_at": "2025-12-31",
        "source_geography": "town",
        "confidence": "high",
        "synthetic": "false",
        **values,
    }
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerow(row)


def _write_manifest(path: Path, records: list[dict[str, str]]) -> None:
    from lifescape.evidence_audit import MANIFEST_COLUMNS

    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(records)


def _manifest_record(metric_id: str, value: str, **overrides: str) -> dict[str, str]:
    return {
        "place_id": "town",
        "metric_id": metric_id,
        "value": value,
        "source_url": "https://example.gov/data",
        "source_title": "Official town metric",
        "publisher": "Official publisher",
        "tier": "A",
        "retrieved_at": "2026-01-01",
        "observed_period": "2025",
        "observed_at": "2025-12-31",
        "source_geography": "town",
        "confidence": "high",
        "synthetic": "false",
        "notes": "",
        **overrides,
    }


def test_audit_flags_shared_source_without_metric_provenance(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence.csv"
    _write_wide_evidence(
        evidence,
        {"median_sale_price": "400000", "broadband_mbps_down": "1000", "annual_snowfall": "20"},
    )

    audit = audit_manual_evidence(
        evidence, load_metrics(CONFIG_DIR), load_sources(CONFIG_DIR), as_of=None
    )

    assert audit.finding_count == 3
    assert {(entry.metric_id, entry.reason) for entry in audit.entries} == {
        ("median_sale_price", "source title identifies ZHVI, not median sale price"),
        ("broadband_mbps_down", "missing metric-specific provenance record"),
        ("annual_snowfall", "missing metric-specific provenance record"),
    }


def test_audit_accepts_valid_metric_specific_records(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence.csv"
    manifest = tmp_path / "manifest.csv"
    _write_wide_evidence(evidence, {"broadband_mbps_down": "1000", "annual_snowfall": "20"})
    _write_manifest(
        manifest,
        [
            _manifest_record("broadband_mbps_down", "1000"),
            _manifest_record("annual_snowfall", "20"),
        ],
    )

    audit = audit_manual_evidence(
        evidence,
            load_metrics(CONFIG_DIR),
            load_sources(CONFIG_DIR),
        manifest_path=manifest,
        as_of=None,
    )

    assert [entry.status for entry in audit.entries] == ["ready", "ready"]
    assert audit.finding_count == 0


def test_audit_flags_zhvi_as_not_median_sale_price(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence.csv"
    manifest = tmp_path / "manifest.csv"
    _write_wide_evidence(evidence, {"median_sale_price": "400000"})
    _write_manifest(
        manifest,
        [
            _manifest_record(
                "median_sale_price",
                "400000",
                source_title="Town Home Values (ZHVI)",
            )
        ],
    )

    audit = audit_manual_evidence(
        evidence,
            load_metrics(CONFIG_DIR),
            load_sources(CONFIG_DIR),
        manifest_path=manifest,
    )

    assert audit.entries[0].status == "action_required"
    assert audit.entries[0].reason == "source title identifies ZHVI, not median sale price"


def test_audit_rejects_incomplete_metric_specific_provenance(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence.csv"
    manifest = tmp_path / "manifest.csv"
    _write_wide_evidence(evidence, {"annual_snowfall": "20"})
    _write_manifest(manifest, [_manifest_record("annual_snowfall", "20", source_url="")])

    audit = audit_manual_evidence(
        evidence,
            load_metrics(CONFIG_DIR),
            load_sources(CONFIG_DIR),
        manifest_path=manifest,
    )

    assert audit.entries[0].reason == "manifest record is missing fields: source_url"


def test_audit_output_is_deterministic_and_preserves_input(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence.csv"
    _write_wide_evidence(evidence, {"annual_snowfall": "20"})
    original = evidence.read_bytes()
    audit = audit_manual_evidence(
        evidence, load_metrics(CONFIG_DIR), load_sources(CONFIG_DIR)
    )

    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_audit, first_template = write_evidence_audit(audit, first_dir)
    second_audit, second_template = write_evidence_audit(audit, second_dir)

    assert evidence.read_bytes() == original
    assert first_audit.read_bytes() == second_audit.read_bytes()
    assert first_template.read_bytes() == second_template.read_bytes()
    payload = json.loads(first_audit.read_text(encoding="utf-8"))
    assert payload["entries"][0]["supplied_source_url"] == "https://www.zillow.com/home-values/town"
    assert [row["metric_id"] for row in csv.DictReader(first_template.open(encoding="utf-8"))] == [
        "annual_snowfall"
    ]
