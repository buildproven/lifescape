"""Hard-gate evaluation: only PASS and documented WAIVED candidates proceed."""

from __future__ import annotations

from collections.abc import Mapping

from retirement_engine.evidence import SourcePolicyError, validate_source
from retirement_engine.models import (
    GateDefinition,
    GateOperator,
    GateResult,
    GateState,
    ObservationRecord,
    SourcesConfig,
)


def evaluate_gates(
    place_ids: tuple[str, ...],
    observations: tuple[ObservationRecord, ...],
    definitions: tuple[GateDefinition, ...],
    policy: SourcesConfig,
    waivers: Mapping[tuple[str, str], str] | None = None,
) -> tuple[GateResult, ...]:
    indexed = {(item.place.place_id, item.metric_id): item for item in observations}
    documented_waivers = waivers or {}
    results: list[GateResult] = []
    for place_id in sorted(place_ids):
        for gate in definitions:
            waiver = documented_waivers.get((place_id, gate.id))
            if waiver:
                results.append(
                    GateResult(
                        place_id=place_id,
                        gate_id=gate.id,
                        result=GateState.WAIVED,
                        raw_value=None,
                        threshold=gate.threshold,
                        source_url=None,
                        notes=f"Explicit waiver: {waiver}",
                    )
                )
                continue
            observation = indexed.get((place_id, gate.metric_id))
            if observation is None:
                results.append(
                    GateResult(
                        place_id=place_id,
                        gate_id=gate.id,
                        result=GateState.UNKNOWN,
                        raw_value=None,
                        threshold=gate.threshold,
                        source_url=None,
                        notes="Blocking: required metric has no eligible evidence",
                    )
                )
                continue
            try:
                validate_source(observation.source, policy, for_gate=True)
            except SourcePolicyError as exc:
                results.append(
                    GateResult(
                        place_id=place_id,
                        gate_id=gate.id,
                        result=GateState.UNKNOWN,
                        raw_value=observation.raw_value,
                        threshold=gate.threshold,
                        source_url=observation.source.url,
                        notes=f"Blocking: {exc}",
                    )
                )
                continue
            passed = (
                observation.raw_value >= gate.threshold
                if gate.operator is GateOperator.MIN
                else observation.raw_value <= gate.threshold
            )
            results.append(
                GateResult(
                    place_id=place_id,
                    gate_id=gate.id,
                    result=GateState.PASS if passed else GateState.FAIL,
                    raw_value=observation.raw_value,
                    threshold=gate.threshold,
                    source_url=observation.source.url,
                    notes="Threshold satisfied" if passed else "Blocking: threshold failed",
                )
            )
    return tuple(results)


def eligible_places(results: tuple[GateResult, ...]) -> tuple[str, ...]:
    states: dict[str, list[GateState]] = {}
    for result in results:
        states.setdefault(result.place_id, []).append(result.result)
    return tuple(
        sorted(
            place_id
            for place_id, place_states in states.items()
            if place_states
            and all(state in {GateState.PASS, GateState.WAIVED} for state in place_states)
        )
    )
