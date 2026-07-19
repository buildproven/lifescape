from lifescape.gates import eligible_places, evaluate_gates
from lifescape.models import (
    Confidence,
    GateDefinition,
    GateOperator,
    GateState,
)


def test_gate_pass_fail_unknown_and_low_confidence_block(policy, observation_factory) -> None:
    definitions = (
        GateDefinition(id="healthcare", metric_id="er", operator=GateOperator.MAX, threshold=25),
    )
    observations = (
        observation_factory("pass", "er", 10),
        observation_factory("fail", "er", 30),
        observation_factory("low", "er", 10, confidence=Confidence.LOW),
    )
    results = evaluate_gates(("pass", "fail", "missing", "low"), observations, definitions, policy)
    states = {item.place_id: item.result for item in results}
    assert states == {
        "fail": GateState.FAIL,
        "low": GateState.UNKNOWN,
        "missing": GateState.UNKNOWN,
        "pass": GateState.PASS,
    }
    assert eligible_places(results) == ("pass",)


def test_documented_waiver_can_proceed(policy) -> None:
    definition = GateDefinition(id="winter", metric_id="snow", operator="max", threshold=20)
    results = evaluate_gates(
        ("town",), (), (definition,), policy, {("town", "winter"): "User accepts winter"}
    )
    assert results[0].result is GateState.WAIVED
    assert "User accepts winter" in results[0].notes
    assert eligible_places(results) == ("town",)
