from retirement_engine.models import Direction, MetricDefinition, WeightsConfig
from retirement_engine.scoring import score_places


def test_scoring_ranks_and_penalizes_missing_data(observation_factory) -> None:
    metrics = (
        MetricDefinition(
            id="nature", name="Nature", unit="index", direction=Direction.HIGHER, criterion="nature"
        ),
        MetricDefinition(
            id="social", name="Social", unit="index", direction=Direction.HIGHER, criterion="social"
        ),
    )
    config = WeightsConfig(weights={"nature": 50, "social": 50}, missing_noncritical_penalty=2)
    observations = (
        observation_factory("a", "nature", 10),
        observation_factory("a", "social", 10),
        observation_factory("b", "nature", 0),
    )
    scores = score_places(("a", "b"), observations, metrics, config)
    assert scores[0].place_id == "a"
    b_social = next(item for item in scores[1].criteria if item.criterion == "social")
    assert b_social.missing_penalty == 2
    assert b_social.normalized_score == 3


def test_missing_critical_metric_scores_zero_and_stays_visible() -> None:
    metrics = (
        MetricDefinition(
            id="critical",
            name="Critical",
            unit="index",
            direction=Direction.HIGHER,
            criterion="safety",
            critical=True,
        ),
    )
    scores = score_places(
        ("town",),
        (),
        metrics,
        WeightsConfig(weights={"safety": 100}, missing_noncritical_penalty=2),
    )

    criterion = scores[0].criteria[0]
    assert criterion.normalized_score == 0
    assert criterion.missing_critical is True
