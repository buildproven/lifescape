"""Local durable storage for research packets outside the decision engine."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from lifescape.models import StrictModel
from lifescape.research import PromotionResult, ResearchPacket, ReviewRecord
from lifescape.research_sources import EvidenceFetchResult


class FetchSnapshot(StrictModel):
    """One immutable fetch result retained for refresh/audit comparison."""

    fetched_at: datetime
    result: EvidenceFetchResult


class StoredResearchPacket(StrictModel):
    """A packet plus its review and fetch history; never an engine input."""

    packet: ResearchPacket
    promotions: tuple[PromotionResult, ...] = ()
    reviews: tuple[ReviewRecord, ...] = ()
    fetch_history: tuple[FetchSnapshot, ...] = ()


class ResearchWorkspace:
    """Atomically persist local packet history without sharing or scoring it."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def load(self) -> tuple[StoredResearchPacket, ...]:
        if not self.root.exists():
            return ()
        packets: list[StoredResearchPacket] = []
        for path in sorted(self.root.glob("*.json")):
            try:
                packets.append(
                    StoredResearchPacket.model_validate_json(path.read_text(encoding="utf-8"))
                )
            except (OSError, ValueError) as exc:
                raise ValueError(f"cannot load local research packet {path.name}: {exc}") from exc
        return tuple(packets)

    def save(self, stored: StoredResearchPacket) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / f"{stored.packet.id}.json"
        temporary = self.root / f".{stored.packet.id}.{uuid4().hex}.tmp"
        try:
            temporary.write_text(stored.model_dump_json(indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def snapshot(result: EvidenceFetchResult) -> FetchSnapshot:
        return FetchSnapshot(fetched_at=datetime.now(UTC), result=result)
