from retirement_engine.gates import evaluate_gates
from retirement_engine.models import GateDefinition, GateState


def test_missing_critical_metric_is_blocking_unknown(policy) -> None:
    result = evaluate_gates(
        ("town",),
        (),
        (GateDefinition(id="broadband", metric_id="speed", operator="min", threshold=100),),
        policy,
    )[0]
    assert result.result is GateState.UNKNOWN
    assert result.raw_value is None
