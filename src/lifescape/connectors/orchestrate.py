"""Runs connectors for a set of places and folds failures into missing evidence.

A connector failure for any (place, metric) pair is logged and the pair is simply
absent from the returned observations — gates.evaluate_gates already treats a missing
observation as UNKNOWN and blocks the place, so no separate failure-handling path is
needed downstream. Connectors never abort the run.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence

from lifescape.connectors.base import Connector, DataRequest
from lifescape.models import ObservationRecord, PlaceRecord, StrictModel

EventSink = Callable[[str, dict[str, object]], None]


class PlaceRequest(StrictModel):
    """A place to fetch live evidence for, identified by this engine's place_id."""

    place_id: str
    geography: str


def fetch_live_observations(
    connectors: Sequence[Connector],
    places: Iterable[PlaceRequest],
    *,
    on_event: EventSink | None = None,
) -> tuple[ObservationRecord, ...]:
    """Fetch, normalize, and validate evidence from each connector for each place.

    Any per-(connector, place) failure — a connector error, an invalid ValidationResult,
    or a place the connector has no data for — is reported via on_event and skipped.
    The caller's place_id always wins over whatever identity a connector's normalize()
    assigns, so live observations line up with the engine's own place records.
    """
    observations: list[ObservationRecord] = []
    places = list(places)
    for connector in connectors:
        metric_ids = tuple(sorted(connector.supported_metric_ids))
        if not metric_ids:
            continue
        for place in places:
            request = DataRequest(geography=place.geography, metric_ids=metric_ids)
            try:
                response = connector.fetch(request)
                fetched = connector.normalize(response)
            except (ValueError, OSError) as exc:
                _emit(
                    on_event,
                    "connector_fetch_failed",
                    connector=connector.name,
                    place_id=place.place_id,
                    error=str(exc),
                )
                continue
            rebound = [
                observation.model_copy(
                    update={
                        "place": _rebind_place(observation.place, place.place_id),
                    }
                )
                for observation in fetched
            ]
            result = connector.validate(rebound)
            if not result.valid:
                _emit(
                    on_event,
                    "connector_validation_failed",
                    connector=connector.name,
                    place_id=place.place_id,
                    errors=list(result.errors),
                )
                continue
            observations.extend(rebound)
    return tuple(observations)


def _rebind_place(place: PlaceRecord, place_id: str) -> PlaceRecord:
    return place.model_copy(update={"place_id": place_id})


def _emit(on_event: EventSink | None, event: str, **fields: object) -> None:
    if on_event is not None:
        on_event(event, fields)
