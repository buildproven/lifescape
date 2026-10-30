"""Adapter-backed evidence retrieval for local research packets.

Discovery leads remain Tier C until a human approves a fetched observation.  This
module owns the seam between a packet and the existing connector protocol; it does
not turn missing geography or connector failures into guessed values.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Protocol

from pydantic import Field

from lifescape.connectors import CensusAcsConnector, Connector, NoaaGsoyConnector
from lifescape.connectors.orchestrate import PlaceRequest, fetch_live_observations
from lifescape.models import ObservationRecord, StrictModel
from lifescape.research import ResearchPacket


class EvidenceFetchResult(StrictModel):
    """Fetched observations plus explicit per-place/connector failures."""

    observations: tuple[ObservationRecord, ...] = ()
    errors: dict[str, tuple[str, ...]] = Field(default_factory=dict)


class ResearchEvidenceProvider(Protocol):
    """Fetch public-source observations without approving them."""

    def fetch(self, packet: ResearchPacket) -> EvidenceFetchResult: ...


class ConnectorEvidenceProvider:
    """Run the existing connectors for packet leads with explicit geographies.

    Geography mappings are system configuration, not user evidence.  A lead may
    carry a mapping supplied by a trusted discovery fixture; otherwise the provider
    reports that the connector geography is unavailable.  In particular, NOAA never
    chooses a nearest station on behalf of a user.
    """

    def __init__(
        self,
        *,
        connectors: tuple[Connector, ...] | None = None,
        geographies: Mapping[str, Mapping[str, str]] | None = None,
    ) -> None:
        self._connectors = connectors or (CensusAcsConnector(), NoaaGsoyConnector())
        self._geographies = {
            place_id: dict(values) for place_id, values in (geographies or {}).items()
        }

    @classmethod
    def from_environment(cls) -> ConnectorEvidenceProvider:
        """Load optional system geography configuration from one JSON variable.

        The value is intentionally configuration-only and never contains metric
        observations.  Example: ``{"asheville_nc":{"census_acs":"37:0210400",
        "noaa_gsoy":"USW00003812:2024"}}``.
        """
        raw = os.environ.get("LIFESCAPE_RESEARCH_GEOGRAPHIES", "")
        if not raw:
            return cls()
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("LIFESCAPE_RESEARCH_GEOGRAPHIES must be valid JSON") from exc
        if not isinstance(decoded, dict):
            raise ValueError("LIFESCAPE_RESEARCH_GEOGRAPHIES must be an object")
        geographies: dict[str, dict[str, str]] = {}
        for place_id, values in decoded.items():
            if not isinstance(place_id, str) or not isinstance(values, dict):
                raise ValueError("research geography entries must map place IDs to objects")
            if not all(
                isinstance(key, str) and isinstance(value, str) for key, value in values.items()
            ):
                raise ValueError("research geography connector values must be strings")
            geographies[place_id] = dict(values)
        return cls(geographies=geographies)

    def fetch(self, packet: ResearchPacket) -> EvidenceFetchResult:
        errors: dict[str, list[str]] = {}
        requests: list[PlaceRequest] = []
        supported = {
            connector.name: connector.supported_metric_ids for connector in self._connectors
        }
        for lead in packet.leads:
            configured = dict(self._geographies.get(lead.place.place_id, {}))
            configured.update(lead.connector_geographies)
            missing = [
                connector_name for connector_name in supported if connector_name not in configured
            ]
            if missing:
                errors.setdefault(lead.place.place_id, []).extend(
                    f"{connector_name}: explicit source geography is not configured"
                    for connector_name in missing
                )
            requests.append(
                PlaceRequest(
                    place_id=lead.place.place_id,
                    connector_geographies=configured,
                    place=lead.place,
                )
            )

        def on_event(event: str, fields: dict[str, object]) -> None:
            place_id = str(fields.get("place_id", "unknown"))
            connector = str(fields.get("connector", "connector"))
            if event == "connector_fetch_failed":
                reason = str(fields.get("error", "fetch failed"))
            else:
                raw_errors = fields.get("errors", ())
                errors_for_event = raw_errors if isinstance(raw_errors, (list, tuple)) else ()
                reason = "; ".join(str(error) for error in errors_for_event)
            errors.setdefault(place_id, []).append(f"{connector}: {reason}")

        observations = fetch_live_observations(
            self._connectors,
            requests,
            on_event=on_event,
        )
        return EvidenceFetchResult(
            observations=observations,
            errors={place_id: tuple(values) for place_id, values in sorted(errors.items())},
        )
