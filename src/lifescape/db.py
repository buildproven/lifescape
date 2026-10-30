"""SQLite persistence for reproducible research runs."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    create_engine,
    event,
)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from lifescape.models import (
    GateDefinition,
    GateResult,
    MetricDefinition,
    ObservationRecord,
    PlaceScore,
    SensitivityResult,
)


class Base(DeclarativeBase):
    pass


class ResearchRunRow(Base):
    __tablename__ = "research_runs"
    run_id: Mapped[str] = mapped_column(String, primary_key=True)
    profile_version: Mapped[str] = mapped_column(String)
    config_hash: Mapped[str] = mapped_column(String)
    engine_version: Mapped[str] = mapped_column(String)
    evaluated_as_of: Mapped[str] = mapped_column(String)
    evidence_through: Mapped[datetime] = mapped_column(DateTime)
    simulations: Mapped[int] = mapped_column(Integer)
    sensitivity_seed: Mapped[int] = mapped_column(Integer)
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
    confidence: Mapped[str] = mapped_column(String)
    license_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    checksum: Mapped[str | None] = mapped_column(String, nullable=True)
    synthetic: Mapped[bool] = mapped_column(Boolean)


class MetricRow(Base):
    __tablename__ = "metrics"
    run_id: Mapped[str] = mapped_column(ForeignKey("research_runs.run_id"), primary_key=True)
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
    __table_args__ = (
        ForeignKeyConstraint(["run_id", "metric_id"], ["metrics.run_id", "metrics.metric_id"]),
    )
    observation_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String)
    place_id: Mapped[str] = mapped_column(ForeignKey("places.place_id"))
    metric_id: Mapped[str] = mapped_column(String)
    raw_value: Mapped[float] = mapped_column(Float)
    observed_period: Mapped[str] = mapped_column(String)
    observed_at: Mapped[str] = mapped_column(String)
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
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.source_id"), nullable=True)
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
    missing_critical: Mapped[bool] = mapped_column(Boolean)


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


class SensitivityRow(Base):
    __tablename__ = "sensitivity_results"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("research_runs.run_id"))
    place_id: Mapped[str] = mapped_column(ForeignKey("places.place_id"))
    top_three_frequency: Mapped[float] = mapped_column(Float)
    mean_rank: Mapped[float] = mapped_column(Float)
    rank_variance: Mapped[float] = mapped_column(Float)
    fragile: Mapped[bool] = mapped_column(Boolean)


def initialize_database(path: Path) -> tuple[Session, Engine]:
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{path}", connect_args={"timeout": 30.0})
    event.listen(
        engine,
        "connect",
        lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"),
    )
    event.listen(
        engine,
        "connect",
        lambda connection, _: connection.execute("PRAGMA busy_timeout=30000"),
    )
    with engine.connect() as connection:
        connection.exec_driver_sql("BEGIN EXCLUSIVE")
        try:
            Base.metadata.create_all(connection)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    return Session(engine), engine


def persist_run(
    session: Session,
    *,
    run_id: str,
    profile_version: str,
    config_hash: str,
    engine_version: str,
    evaluated_as_of: date,
    evidence_through: datetime,
    simulations: int,
    sensitivity_seed: int,
    metrics: tuple[MetricDefinition, ...],
    gate_definitions: tuple[GateDefinition, ...],
    observations: tuple[ObservationRecord, ...],
    gates: tuple[GateResult, ...],
    scores: tuple[PlaceScore, ...],
    sensitivity: tuple[SensitivityResult, ...],
) -> bool:
    """Persist one fully evaluated run in a single transaction."""
    executed_at = datetime.now(UTC)
    claimed_run_id = session.scalar(
        sqlite_insert(ResearchRunRow)
        .values(
            run_id=run_id,
            profile_version=profile_version,
            config_hash=config_hash,
            engine_version=engine_version,
            evaluated_as_of=evaluated_as_of.isoformat(),
            evidence_through=evidence_through,
            simulations=simulations,
            sensitivity_seed=sensitivity_seed,
            started_at=executed_at,
            completed_at=executed_at,
            status="completed",
        )
        .on_conflict_do_nothing(index_elements=["run_id"])
        .returning(ResearchRunRow.run_id)
    )
    if claimed_run_id is None:
        existing = session.get(ResearchRunRow, run_id)
        existing_status = existing.status if existing is not None else None
        session.rollback()
        if existing_status == "completed":
            return False
        raise ValueError(f"run {run_id!r} exists in incomplete state {existing_status!r}")
    places = {observation.place.place_id: observation.place for observation in observations}
    for place in places.values():
        session.execute(
            sqlite_insert(PlaceRow)
            .values(
                place_id=place.place_id,
                name=place.name,
                state=place.state,
                geography_type=place.geography_type,
            )
            .on_conflict_do_nothing(index_elements=["place_id"])
        )
        existing_place = session.get(PlaceRow, place.place_id)
        if existing_place is None:
            raise RuntimeError(f"failed to persist place {place.place_id!r}")
        if (
            existing_place.name,
            existing_place.state,
            existing_place.geography_type,
        ) != (place.name, place.state, place.geography_type):
            raise ValueError(f"place identity changed for {place.place_id!r}")
    gate_by_metric = {gate.metric_id: gate.id for gate in gate_definitions}
    for metric in metrics:
        session.add(
            MetricRow(
                run_id=run_id,
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
    source_ids: dict[tuple[object, ...], int] = {}
    observation_source_ids: dict[tuple[str, str], int] = {}
    for observation in observations:
        key = (
            observation.source.url,
            observation.source.title,
            observation.source.publisher,
            observation.source.tier,
            observation.source.retrieved_at,
            observation.source.geography,
            observation.source.confidence,
            observation.source.synthetic,
        )
        if key not in source_ids:
            row = SourceRow(
                url=observation.source.url,
                title=observation.source.title,
                publisher=observation.source.publisher,
                tier=observation.source.tier,
                retrieved_at=observation.source.retrieved_at.isoformat(),
                geography=observation.source.geography,
                confidence=observation.source.confidence,
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
                observed_at=observation.observed_at.isoformat(),
                retrieved_at=observation.source.retrieved_at.isoformat(),
                source_id=source_ids[key],
                confidence=observation.source.confidence,
                notes="synthetic benchmark" if observation.source.synthetic else "",
            )
        )
        observation_source_ids[(observation.place.place_id, observation.metric_id)] = source_ids[
            key
        ]
    gate_metric_ids = {gate.id: gate.metric_id for gate in gate_definitions}
    for gate in gates:
        session.add(
            GateResultRow(
                run_id=run_id,
                place_id=gate.place_id,
                gate_id=gate.gate_id,
                result=gate.result,
                raw_value=gate.raw_value,
                threshold=gate.threshold,
                source_id=observation_source_ids.get(
                    (gate.place_id, gate_metric_ids[gate.gate_id])
                ),
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
                    missing_critical=criterion.missing_critical,
                )
            )
    for result in sensitivity:
        session.add(
            SensitivityRow(
                run_id=run_id,
                place_id=result.place_id,
                top_three_frequency=result.top_three_frequency,
                mean_rank=result.mean_rank,
                rank_variance=result.rank_variance,
                fragile=result.fragile,
            )
        )
    session.commit()
    return True
