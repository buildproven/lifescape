"""One store for the discovery and evidence records the MCP server keeps between calls.

A hosted server has no Mac disk (machine-setup ``docs/AGENT-FIRST.md``), so research packets
(discovery leads, fetched proposals, reviews, promotions) and run report indexes go through
``Store``. ``LIFESCAPE_STORE`` picks the adapter: ``local`` (the default, for development and the
stdio server) keeps JSON files under the output directory; ``database`` keeps them in the
SQLAlchemy database named by ``DATABASE_URL`` (``lifescape.store_db``). A database
that cannot be reached is an error naming the store; nothing falls back to local files.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from lifescape.models import StrictModel
from lifescape.research_workspace import ResearchWorkspace, StoredResearchPacket

STORE_ENV = "LIFESCAPE_STORE"
# The name the Vercel Neon integration sets.
DATABASE_URL_ENV = "DATABASE_URL"
LOCAL_STORE = "local"
DATABASE_STORE = "database"
RESEARCH_WORKSPACE = "research-workspace"
RUN_INDEX = "runs"
PACKET_ID = re.compile(r"[a-f0-9]{12}")
RUN_ID = re.compile(r"[a-f0-9]{16}")


class StoreError(RuntimeError):
    """A store cannot serve the request; the message names the store."""


class StoreConfigError(StoreError):
    """The store settings cannot select or build an adapter."""


class StoreUnavailableError(StoreError):
    """The selected store cannot be reached."""


class RunReportIndex(StrictModel):
    """The reports one ``compare_finalists`` run wrote, with digests to detect overwrites."""

    run_id: str
    reports: dict[str, str]
    sha256: dict[str, str]


class Store(Protocol):
    """Read and write research packets and run report indexes, by ID."""

    @property
    def label(self) -> str:
        """Names the adapter and where it keeps records, without credentials."""
        ...

    def load_packets(self) -> tuple[StoredResearchPacket, ...]:
        """Every stored packet, in packet ID order."""
        ...

    def get_packet(self, packet_id: str) -> StoredResearchPacket | None: ...

    def save_packet(self, stored: StoredResearchPacket) -> None:
        """Insert the packet, or replace the stored packet with the same ID."""
        ...

    def get_run_index(self, run_id: str) -> RunReportIndex | None: ...

    def save_run_index(self, index: RunReportIndex) -> None:
        """Insert the index, or replace the stored index with the same run ID."""
        ...


def check_packet_id(packet_id: str) -> str:
    if PACKET_ID.fullmatch(packet_id) is None:
        raise ValueError(f"packet ID must be 12 lowercase hex characters: {packet_id!r}")
    return packet_id


def check_run_id(run_id: str) -> str:
    if RUN_ID.fullmatch(run_id) is None:
        raise ValueError(f"run ID must be 16 lowercase hex characters: {run_id!r}")
    return run_id


class LocalStore:
    """JSON files under the output directory: ``research-workspace/`` and ``runs/``."""

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self._workspace = ResearchWorkspace(output_dir / RESEARCH_WORKSPACE)
        self._runs = output_dir / RUN_INDEX

    @property
    def label(self) -> str:
        return f"local store at {self.output_dir}"

    def load_packets(self) -> tuple[StoredResearchPacket, ...]:
        return self._workspace.load()

    def get_packet(self, packet_id: str) -> StoredResearchPacket | None:
        check_packet_id(packet_id)
        return next((item for item in self.load_packets() if item.packet.id == packet_id), None)

    def save_packet(self, stored: StoredResearchPacket) -> None:
        check_packet_id(stored.packet.id)
        self._workspace.save(stored)

    def get_run_index(self, run_id: str) -> RunReportIndex | None:
        path = self._runs / f"{check_run_id(run_id)}.json"
        if not path.is_file():
            return None
        try:
            return RunReportIndex.model_validate_json(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise ValueError(f"cannot load report index of run {run_id} at {path}: {exc}") from exc

    def save_run_index(self, index: RunReportIndex) -> None:
        path = self._runs / f"{check_run_id(index.run_id)}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(index.model_dump_json(indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def store_from_env(output_dir: Path, environ: Mapping[str, str] | None = None) -> Store:
    """The adapter ``LIFESCAPE_STORE`` names; unset means the local adapter."""
    settings = os.environ if environ is None else environ
    kind = settings.get(STORE_ENV, LOCAL_STORE)
    if kind == LOCAL_STORE:
        return LocalStore(output_dir)
    if kind != DATABASE_STORE:
        raise StoreConfigError(
            f"{STORE_ENV} must be '{LOCAL_STORE}' or '{DATABASE_STORE}', not {kind!r}"
        )
    url = settings.get(DATABASE_URL_ENV, "").strip()
    if not url:
        raise StoreConfigError(f"{STORE_ENV}={DATABASE_STORE} needs {DATABASE_URL_ENV} set")
    # Imported here: the database adapter imports this module for the interface types.
    from lifescape.store_db import DatabaseStore

    return DatabaseStore.from_url(url)
