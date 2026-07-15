"""SQLite persistence for reproducible research runs."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, Text, create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from retirement_engine.models import (
    GateDefinition,
    GateResult,
    MetricDefinition,
    ObservationRecord,
    PlaceScore,
)


class Base(DeclarativeBase):
    pass


class ResearchRunRow(Base):
    __tablename__ = "research_runs"
    run_id: Mapped[str] = mapped_column(String, primary_key=True)
    profile_version: Mapped[str] = mapped_column(String)
    config_hash: Mapped[str] = mapped_column(String)
    started_at: Mapped[datetime] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String)


class PlaceRow(Base):
    __tablename__ = "places"
    place_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    state: Mapped[str] = mapped_column(String(2))
    geography_type: Mapped[str] = mapped_column(String)
    county: Mapped[str | None] = mapped_column(String, nullable=True)
    metro: Mapped[str | None] = mapped_column(String, nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    population: Mapped[int | None] = mapped_column(nullable=True)


class SourceRow(Base):
    __tablename__ = "sources"
    source_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    url: Mapped[str] = mapped_column(String)
    title: Mapped[str] = mapped_column(String)
    publisher: Mapped[str] = mapped_column(String)
    tier: Mapped[str] = mapped_column(String(1))
    retrieved_at: Mapped[str] = mapped_column(String)
    publication_date: Mapped[str | None] = mapped_column(String, nullable=True)
    geography: Mapped[str] = mapped_column(String)
    license_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    checksum: Mapped[str | None] = mapped_column(String, nullable=True)
    synthetic: Mapped[bool] = mapped_column(Boolean)


class MetricRow(Base):
    __tablename__ = "metrics"
    metric_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    unit: Mapped[str] = mapped_column(String)
    direction: Mapped[str] = mapped_column(String)
    freshness_rule: Mapped[str] = mapped_column(String)
    geography_level: Mapped[str] = mapped_column(String)
    criterion: Mapped[str] = mapped_column(String)
    gate_id: Mapped[str | None] = mapped_column(String, nullable=True)


class ObservationRow(Base):
    __tablename__ = "observations"
    observation_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("research_runs.run_id"))
    place_id: Mapped[str] = mapped_column(ForeignKey("places.place_id"))
    metric_id: Mapped[str] = mapped_column(ForeignKey("metrics.metric_id"))
    raw_value: Mapped[float] = mapped_column(Float)
    observed_period: Mapped[str] = mapped_column(String)
    retrieved_at: Mapped[str] = mapped_column(String)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.source_id"))
    confidence: Mapped[str] = mapped_column(String)
    notes: Mapped[str] = mapped_column(Text, default="")


class GateResultRow(Base):
    __tablename__ = "gate_results"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("research_runs.run_id"))
    place_id: Mapped[str] = mapped_column(ForeignKey("places.place_id"))
    gate_id: Mapped[str] = mapped_column(String)
    result: Mapped[str] = mapped_column(String)
    raw_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    threshold: Mapped[float] = mapped_column(Float)
    source_id: Mapped[int | None] = mapped_column(nullable=True)
    notes: Mapped[str] = mapped_column(Text)


class ScoreRow(Base):
    __tablename__ = "scores"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("research_runs.run_id"))
    place_id: Mapped[str] = mapped_column(ForeignKey("places.place_id"))
    criterion: Mapped[str] = mapped_column(String)
    normalized_score: Mapped[float] = mapped_column(Float)
    weight: Mapped[float] = mapped_column(Float)
    weighted_score: Mapped[float] = mapped_column(Float)
    missing_penalty: Mapped[float] = mapped_column(Float)


class UnknownRow(Base):
    __tablename__ = "unknowns"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("research_runs.run_id"))
    entity_type: Mapped[str] = mapped_column(String)
    entity_id: Mapped[str] = mapped_column(String)
    question: Mapped[str] = mapped_column(Text)
    resolution_action: Mapped[str] = mapped_column(Text)
    blocking: Mapped[bool] = mapped_column(Boolean)
    status: Mapped[str] = mapped_column(String)


def initialize_database(path: Path) -> tuple[Session, Engine]:
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    return Session(engine), engine


def persist_run(
    session: Session,
    *,
    run_id: str,
    config_hash: str,
    generated_at: datetime,
    metrics: tuple[MetricDefinition, ...],
    gate_definitions: tuple[GateDefinition, ...],
    observations: tuple[ObservationRecord, ...],
    gates: tuple[GateResult, ...],
    scores: tuple[PlaceScore, ...],
) -> None:
    """Persist one fully evaluated run in a single transaction."""
    if session.get(ResearchRunRow, run_id) is not None:
        return
    session.add(
        ResearchRunRow(
            run_id=run_id,
            profile_version="1.0",
            config_hash=config_hash,
            started_at=generated_at,
            completed_at=generated_at,
            status="completed",
        )
    )
    places = {observation.place.place_id: observation.place for observation in observations}
    for place in places.values():
        session.add(
            PlaceRow(
                place_id=place.place_id,
                name=place.name,
                state=place.state,
                geography_type=place.geography_type,
            )
        )
    gate_by_metric = {gate.metric_id: gate.id for gate in gate_definitions}
    for metric in metrics:
        if session.get(MetricRow, metric.id) is None:
            session.add(
                MetricRow(
                    metric_id=metric.id,
                    name=metric.name,
                    unit=metric.unit,
                    direction=metric.direction,
                    freshness_rule=f"{metric.freshness_days} days",
                    geography_level=metric.geography_level,
                    criterion=metric.criterion,
                    gate_id=gate_by_metric.get(metric.id),
                )
            )
    source_ids: dict[tuple[str, str], int] = {}
    for observation in observations:
        key = (observation.source.url, observation.source.retrieved_at.isoformat())
        if key not in source_ids:
            row = SourceRow(
                url=observation.source.url,
                title=observation.source.title,
                publisher=observation.source.publisher,
                tier=observation.source.tier,
                retrieved_at=observation.source.retrieved_at.isoformat(),
                geography=observation.source.geography,
                synthetic=observation.source.synthetic,
            )
            session.add(row)
            session.flush()
            source_ids[key] = row.source_id
        session.add(
            ObservationRow(
                run_id=run_id,
                place_id=observation.place.place_id,
                metric_id=observation.metric_id,
                raw_value=observation.raw_value,
                observed_period=observation.observed_period,
                retrieved_at=observation.source.retrieved_at.isoformat(),
                source_id=source_ids[key],
                confidence=observation.source.confidence,
                notes="synthetic benchmark" if observation.source.synthetic else "",
            )
        )
    for gate in gates:
        session.add(
            GateResultRow(
                run_id=run_id,
                place_id=gate.place_id,
                gate_id=gate.gate_id,
                result=gate.result,
                raw_value=gate.raw_value,
                threshold=gate.threshold,
                notes=gate.notes,
            )
        )
        if gate.result == "UNKNOWN":
            session.add(
                UnknownRow(
                    run_id=run_id,
                    entity_type="place",
                    entity_id=gate.place_id,
                    question=f"Resolve evidence for gate {gate.gate_id}",
                    resolution_action="Obtain current Tier A/B evidence at the correct geography",
                    blocking=True,
                    status="open",
                )
            )
    for place_score in scores:
        for criterion in place_score.criteria:
            session.add(
                ScoreRow(
                    run_id=run_id,
                    place_id=place_score.place_id,
                    criterion=criterion.criterion,
                    normalized_score=criterion.normalized_score,
                    weight=criterion.weight,
                    weighted_score=criterion.weighted_score,
                    missing_penalty=criterion.missing_penalty,
                )
            )
    session.commit()
