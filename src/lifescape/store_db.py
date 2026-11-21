"""Database adapter for ``lifescape.store``: the hosted server's records in SQL tables.

Any SQLAlchemy URL whose driver is installed works. A ``postgres://`` or ``postgresql://`` URL
(the form Neon's Vercel integration sets in ``DATABASE_URL``) connects through psycopg 3, the
Postgres driver Lifescape declares; SQLAlchemy would otherwise pick psycopg2, and it does not
accept ``postgres://`` at all. The tables hold each record as the same JSON
document the local adapter writes, keyed by its ID, so both adapters give the same results. The
tables are created on first use. A connection or driver failure raises
``StoreUnavailableError`` naming the store, with the password masked.

The same database holds the OAuth codes and refresh tokens already spent (``claim_grant``), so
every hosted replica refuses a grant any other replica has exchanged (BUI-2027).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Self

from sqlalchemy import (
    Column,
    Connection,
    Engine,
    Float,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    delete,
    insert,
    select,
    update,
)
from sqlalchemy.engine import make_url
from sqlalchemy.exc import (
    ArgumentError,
    DBAPIError,
    IntegrityError,
    InterfaceError,
    NoSuchModuleError,
    OperationalError,
)

from lifescape.research_workspace import StoredResearchPacket
from lifescape.store import (
    RunReportIndex,
    StoreConfigError,
    StoreError,
    StoreUnavailableError,
    check_packet_id,
    check_run_id,
)

# Bare Postgres schemes, and the SQLAlchemy dialect+driver they connect through.
POSTGRES_SCHEMES = frozenset({"postgres", "postgresql"})
POSTGRES_DRIVER = "postgresql+psycopg"

METADATA = MetaData()
PACKETS = Table(
    "research_packets",
    METADATA,
    Column("packet_id", String(12), primary_key=True),
    Column("document", Text, nullable=False),
)
RUN_INDEXES = Table(
    "run_report_indexes",
    METADATA,
    Column("run_id", String(16), primary_key=True),
    Column("document", Text, nullable=False),
)
# One row per spent grant ID (``code:<jti>`` or ``refresh:<jti>``) until the grant expires.
SPENT_GRANTS = Table(
    "spent_oauth_grants",
    METADATA,
    Column("grant_id", String(128), primary_key=True),
    Column("expires_at", Float, nullable=False),
)


class DatabaseStore:
    """Research packets and run report indexes in one SQL database."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._schema_ready = False

    @classmethod
    def from_url(cls, url: str) -> Self:
        """Build the adapter without connecting; the first call connects."""
        try:
            parsed = make_url(url)
        except (ArgumentError, ValueError) as exc:
            # The parser's text can quote parts of the URL, such as a password; leave it out.
            raise StoreConfigError(
                "database store URL cannot be parsed; check its scheme, host, and port"
            ) from exc
        if parsed.drivername in POSTGRES_SCHEMES:
            parsed = parsed.set(drivername=POSTGRES_DRIVER)
        masked = parsed.render_as_string(hide_password=True)
        try:
            engine = create_engine(parsed, pool_pre_ping=True)
        except (ArgumentError, NoSuchModuleError, ImportError) as exc:
            raise StoreConfigError(f"database store at {masked} cannot be built: {exc}") from exc
        return cls(engine)

    @property
    def label(self) -> str:
        return f"database store at {self._engine.url.render_as_string(hide_password=True)}"

    @contextmanager
    def _transaction(self) -> Iterator[Connection]:
        """One transaction; a driver failure becomes a ``StoreError`` naming this store."""
        try:
            with self._engine.begin() as connection:
                if not self._schema_ready:
                    METADATA.create_all(connection)
                yield connection
            self._schema_ready = True
        except (OperationalError, InterfaceError) as exc:
            raise StoreUnavailableError(f"{self.label} is unreachable: {_reason(exc)}") from exc
        except DBAPIError as exc:
            raise StoreError(f"{self.label} refused the request: {_reason(exc)}") from exc

    def _document(self, connection: Connection, table: Table, key: str) -> str | None:
        document = connection.execute(
            select(table.c.document).where(table.primary_key.columns[0] == key)
        ).scalar_one_or_none()
        return None if document is None else str(document)

    def _upsert(self, table: Table, key: str, document: str) -> None:
        key_column = table.primary_key.columns[0]
        with self._transaction() as connection:
            updated = connection.execute(
                update(table).where(key_column == key).values(document=document)
            )
            if updated.rowcount == 0:
                connection.execute(
                    insert(table).values({key_column.name: key, "document": document})
                )

    def load_packets(self) -> tuple[StoredResearchPacket, ...]:
        with self._transaction() as connection:
            rows = connection.execute(
                select(PACKETS.c.packet_id, PACKETS.c.document).order_by(PACKETS.c.packet_id)
            ).all()
        return tuple(
            self._parse(StoredResearchPacket.model_validate_json, "research packet", key, doc)
            for key, doc in rows
        )

    def get_packet(self, packet_id: str) -> StoredResearchPacket | None:
        check_packet_id(packet_id)
        with self._transaction() as connection:
            document = self._document(connection, PACKETS, packet_id)
        if document is None:
            return None
        return self._parse(
            StoredResearchPacket.model_validate_json, "research packet", packet_id, document
        )

    def save_packet(self, stored: StoredResearchPacket) -> None:
        self._upsert(PACKETS, check_packet_id(stored.packet.id), stored.model_dump_json())

    def get_run_index(self, run_id: str) -> RunReportIndex | None:
        check_run_id(run_id)
        with self._transaction() as connection:
            document = self._document(connection, RUN_INDEXES, run_id)
        if document is None:
            return None
        return self._parse(
            RunReportIndex.model_validate_json, "report index of run", run_id, document
        )

    def save_run_index(self, index: RunReportIndex) -> None:
        self._upsert(RUN_INDEXES, check_run_id(index.run_id), index.model_dump_json())

    def claim_grant(self, grant_id: str, expires_at: float) -> bool:
        """True on the first claim of ``grant_id`` by any store on this database only.

        The primary key makes the insert the claim: of two replicas inserting one ID, the
        database lets one commit and refuses the other. Rows past their expiry are dropped
        first; a grant past its expiry no longer opens (``exp`` is checked), so dropping its row
        cannot let it back in.
        """
        with self._transaction() as connection:
            connection.execute(delete(SPENT_GRANTS).where(SPENT_GRANTS.c.expires_at <= time.time()))
        try:
            with self._transaction() as connection:
                connection.execute(
                    insert(SPENT_GRANTS).values(grant_id=grant_id, expires_at=expires_at)
                )
        except StoreError as exc:
            # Only a duplicate key means "already spent"; any other failure stays an error.
            if isinstance(exc.__cause__, IntegrityError):
                return False
            raise
        return True

    def _parse[T](self, parse: Callable[[str], T], kind: str, key: object, document: object) -> T:
        try:
            return parse(str(document))
        except ValueError as exc:
            raise ValueError(f"cannot load {kind} {key} from the {self.label}: {exc}") from exc


def _reason(exc: DBAPIError) -> str:
    """The driver's message only: SQLAlchemy's own text repeats the statement and parameters."""
    return f"{type(exc.orig).__name__}: {exc.orig}".strip()
