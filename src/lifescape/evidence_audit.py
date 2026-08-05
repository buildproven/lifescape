"""Audit manual evidence provenance without changing scoring inputs."""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from math import isfinite
from pathlib import Path

from lifescape.evidence import (
    IDENTITY_COLUMNS,
    EvidenceError,
    validate_observation_freshness,
    validate_source,
    validate_unique_headers,
)
from lifescape.models import Confidence, MetricDefinition, SourceRecord, SourcesConfig, SourceTier

MANIFEST_COLUMNS = (
    "place_id",
    "metric_id",
    "value",
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
    "notes",
)


class EvidenceAuditError(EvidenceError):
    """Raised when an evidence audit input is malformed."""


@dataclass(frozen=True)
class AuditEntry:
    """The audit state of one populated manual observation."""

    place_id: str
    place_name: str
    state: str
    metric_id: str
    raw_value: float
    supplied_source_url: str
    validated_provenance: dict[str, str] | None
    status: str
    reason: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "place_id": self.place_id,
            "place_name": self.place_name,
            "state": self.state,
            "metric_id": self.metric_id,
            "raw_value": self.raw_value,
            "supplied_source_url": self.supplied_source_url,
            "validated_provenance": self.validated_provenance,
            "status": self.status,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class EvidenceAudit:
    """Deterministic audit result and a correction-template source."""

    entries: tuple[AuditEntry, ...]
    template_rows: tuple[dict[str, str], ...]
    as_of: date

    @property
    def finding_count(self) -> int:
        return sum(entry.status != "ready" for entry in self.entries)

    def as_dict(self) -> dict[str, object]:
        return {
            "as_of": self.as_of.isoformat(),
            "entries": [entry.as_dict() for entry in self.entries],
            "finding_count": self.finding_count,
            "observation_count": len(self.entries),
        }


def audit_manual_evidence(
    evidence_path: Path,
    metrics: tuple[MetricDefinition, ...],
    policy: SourcesConfig,
    *,
    manifest_path: Path | None = None,
    as_of: date | None = None,
) -> EvidenceAudit:
    """Return metric-level provenance states for a wide manual evidence CSV.

    A wide row's source fields are never assumed to prove every populated metric.
    A separate manifest must attach a complete eligible source record to each metric.
    """
    metric_map = {metric.id: metric for metric in metrics}
    rows = _read_wide_evidence(evidence_path, metric_map)
    manifest = _read_manifest(manifest_path) if manifest_path is not None else {}
    entries: list[AuditEntry] = []
    template_rows: list[dict[str, str]] = []
    reference_date = as_of or date.today()

    for row in rows:
        for metric_id, metric in metric_map.items():
            value = row.get(metric_id, "").strip()
            if not value:
                continue
            try:
                raw_value = float(value)
            except ValueError as exc:
                raise EvidenceAuditError(
                    f"row {row['_row_number']}: {metric_id} must be a numeric value"
                ) from exc
            if not isfinite(raw_value):
                raise EvidenceAuditError(
                    f"row {row['_row_number']}: {metric_id} must be a finite number"
                )
            key = (row["place_id"], metric_id)
            manifest_row = manifest.get(key)
            status, reason, validated_provenance = _audit_status(
                row,
                metric,
                raw_value,
                manifest_row,
                policy,
                reference_date,
            )
            entries.append(
                AuditEntry(
                    place_id=row["place_id"],
                    place_name=row["place_name"],
                    state=row["state"],
                    metric_id=metric_id,
                    raw_value=raw_value,
                    supplied_source_url=row["source_url"],
                    validated_provenance=validated_provenance,
                    status=status,
                    reason=reason,
                )
            )
            template_rows.append(_template_row(row, metric_id, value))

    return EvidenceAudit(
        entries=tuple(entries), template_rows=tuple(template_rows), as_of=reference_date
    )


def write_evidence_audit(
    audit: EvidenceAudit, output_dir: Path, *, manifest_path: Path | None = None
) -> tuple[Path, Path]:
    """Write deterministic audit JSON and a blank per-metric correction template."""
    output_dir.mkdir(parents=True, exist_ok=True)
    audit_path = output_dir / "provenance-audit.json"
    audit_path.write_text(
        json.dumps(audit.as_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    template_path = output_dir / "evidence-manifest-template.csv"
    if manifest_path is not None and template_path.resolve() == manifest_path.resolve():
        raise EvidenceAuditError(
            "output template path would overwrite the supplied evidence manifest"
        )
    with template_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(audit.template_rows)
    return audit_path, template_path


def _read_wide_evidence(
    path: Path, metric_map: dict[str, MetricDefinition]
) -> tuple[dict[str, str], ...]:
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                raise EvidenceAuditError("evidence CSV has no header")
            validate_unique_headers(reader.fieldnames)
            missing = IDENTITY_COLUMNS - set(reader.fieldnames)
            if missing:
                raise EvidenceAuditError(f"evidence CSV missing columns: {sorted(missing)}")
            unknown = set(reader.fieldnames) - IDENTITY_COLUMNS - set(metric_map)
            if unknown:
                raise EvidenceAuditError(f"unknown metric columns: {sorted(unknown)}")
            rows: list[dict[str, str]] = []
            for row_number, raw_row in enumerate(reader, start=2):
                if None in raw_row:
                    raise EvidenceAuditError(f"row {row_number}: has more values than headers")
                row = {key: value or "" for key, value in raw_row.items()}
                row["_row_number"] = str(row_number)
                rows.append(row)
    except OSError as exc:
        raise EvidenceAuditError(f"cannot read {path}: {exc}") from exc
    return tuple(rows)


def _read_manifest(path: Path) -> dict[tuple[str, str], dict[str, str]]:
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                raise EvidenceAuditError("evidence manifest has no header")
            validate_unique_headers(reader.fieldnames)
            missing = set(MANIFEST_COLUMNS) - set(reader.fieldnames)
            if missing:
                raise EvidenceAuditError(f"evidence manifest missing columns: {sorted(missing)}")
            unknown = set(reader.fieldnames) - set(MANIFEST_COLUMNS)
            if unknown:
                raise EvidenceAuditError(
                    f"evidence manifest has unknown columns: {sorted(unknown)}"
                )
            records: dict[tuple[str, str], dict[str, str]] = {}
            for row_number, raw_row in enumerate(reader, start=2):
                if None in raw_row:
                    raise EvidenceAuditError(
                        f"manifest row {row_number}: has more values than headers"
                    )
                row = {key: value or "" for key, value in raw_row.items()}
                key = (row["place_id"], row["metric_id"])
                if not all(key):
                    raise EvidenceAuditError(
                        f"manifest row {row_number}: place_id and metric_id are required"
                    )
                if key in records:
                    raise EvidenceAuditError(
                        f"manifest row {row_number}: duplicate evidence for {key[0]!r}, {key[1]!r}"
                    )
                records[key] = row
    except OSError as exc:
        raise EvidenceAuditError(f"cannot read {path}: {exc}") from exc
    return records


def _audit_status(
    row: dict[str, str],
    metric: MetricDefinition,
    raw_value: float,
    manifest_row: dict[str, str] | None,
    policy: SourcesConfig,
    as_of: date,
) -> tuple[str, str | None, dict[str, str] | None]:
    if manifest_row is None:
        semantic_reason = _semantic_mismatch(metric.id, row["source_title"])
        if semantic_reason is not None:
            return "action_required", semantic_reason, None
        return "action_required", "missing metric-specific provenance record", None
    missing_fields = _missing_manifest_fields(manifest_row)
    if missing_fields:
        return (
            "action_required",
            f"manifest record is missing fields: {', '.join(missing_fields)}",
            None,
        )
    try:
        manifest_value = float(manifest_row["value"])
    except ValueError:
        return "action_required", "manifest value is not numeric", None
    if not isfinite(manifest_value):
        return "action_required", "manifest value must be a finite number", None
    if manifest_value != raw_value:
        return "action_required", "manifest value does not match the wide evidence value", None
    semantic_reason = _semantic_mismatch(metric.id, manifest_row["source_title"])
    if semantic_reason is not None:
        return "action_required", semantic_reason, None
    try:
        source = SourceRecord(
            url=manifest_row["source_url"],
            title=manifest_row["source_title"],
            publisher=manifest_row["publisher"],
            tier=SourceTier(manifest_row["tier"]),
            retrieved_at=date.fromisoformat(manifest_row["retrieved_at"]),
            geography=manifest_row["source_geography"],
            confidence=Confidence(manifest_row["confidence"]),
            synthetic=_parse_boolean(manifest_row["synthetic"]),
        )
        observed_at = date.fromisoformat(manifest_row["observed_at"])
        validate_source(source, policy, for_gate=metric.critical, as_of=as_of)
        validate_observation_freshness(observed_at, metric, source, as_of=as_of)
    except (ValueError, EvidenceError) as exc:
        return "action_required", str(exc), None
    if (
        row["geography_type"] != metric.geography_level
        or source.geography != metric.geography_level
    ):
        return (
            "action_required",
            f"metric {metric.id!r} requires {metric.geography_level!r} geography",
            None,
        )
    return "ready", None, dict(manifest_row)


def _semantic_mismatch(metric_id: str, source_title: str) -> str | None:
    title = source_title.casefold()
    if metric_id == "median_sale_price" and ("zhvi" in title or "zillow home value index" in title):
        return "source title identifies ZHVI, not median sale price"
    return None


def _parse_boolean(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized not in {"true", "false"}:
        raise EvidenceAuditError("manifest synthetic must be true or false")
    return normalized == "true"


def _missing_manifest_fields(row: dict[str, str]) -> tuple[str, ...]:
    required = (
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
    )
    return tuple(field for field in required if not row[field].strip())


def _template_row(row: dict[str, str], metric_id: str, value: str) -> dict[str, str]:
    return {
        "place_id": row["place_id"],
        "metric_id": metric_id,
        "value": value,
        "source_url": "",
        "source_title": "",
        "publisher": "",
        "tier": "",
        "retrieved_at": "",
        "observed_period": "",
        "observed_at": "",
        "source_geography": "",
        "confidence": "",
        "synthetic": "false",
        "notes": "",
    }


def audit_rows_by_place(entries: Iterable[AuditEntry]) -> dict[str, tuple[AuditEntry, ...]]:
    """Return audit entries grouped in stable input order for report consumers."""
    grouped: dict[str, list[AuditEntry]] = {}
    for entry in entries:
        grouped.setdefault(entry.place_id, []).append(entry)
    return {place_id: tuple(rows) for place_id, rows in grouped.items()}
