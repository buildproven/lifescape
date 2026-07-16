"""Common interface required of future public-data connectors."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict

from retirement_engine.models import ObservationRecord


class DataRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    geography: str
    metric_ids: tuple[str, ...]


class RawResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source_url: str
    payload: bytes
    checksum: str


class ValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    valid: bool
    errors: tuple[str, ...] = ()


class Connector(Protocol):
    name: str

    def fetch(self, request: DataRequest) -> RawResponse: ...

    def normalize(self, response: RawResponse) -> list[ObservationRecord]: ...

    def validate(self, observations: list[ObservationRecord]) -> ValidationResult: ...
