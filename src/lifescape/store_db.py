"""Database adapter for ``lifescape.store``: the hosted server's records in SQL tables.

Any SQLAlchemy URL whose driver is installed works. A ``postgres://`` or ``postgresql://`` URL
(the form Neon's Vercel integration sets in ``DATABASE_URL``) connects through psycopg 3, the
Postgres driver Lifescape declares; SQLAlchemy would otherwise pick psycopg2, and it does not
accept ``postgres://`` at all. The tables hold each record as the same JSON
document the local adapter writes, keyed by its ID, so both adapters give the same results. The
tables are created on first use. A connection or driver failure raises
``StoreUnavailableError`` naming the store, with the password masked.

Each packet and run index row has an ``owner``: the signed-in user's subject (the token ``sub``),
or ``NO_USER`` (``''``) for a store with no user (the stdio server, and servers built without
auth). Reads and writes see only the caller's rows, so another user's ID reads as not found
(BUI-2211). A run ID is a hash of the run's inputs, so two users can make the same one: run
indexes are keyed by (owner, run ID). Packet IDs are random, so packets stay keyed by packet
ID, and a save never replaces a packet another user owns.

A database made before rows had an owner migrates on first use, in one transaction:
``research_packets`` gets the ``owner`` column, ``run_report_indexes`` is rebuilt with the new
key, and the rows already there go to ``owner`` (``OWNER_GITHUB_LOGIN``). Only that step assigns
owners, so a row saved later with no user stays the no-user's row. If such rows exist and no
owner is set, the store refuses (``StoreConfigError``) instead of guessing whose they are.

The same database holds the OAuth codes and refresh tokens already spent (``claim_grant``), so
every hosted replica refuses a grant any other replica has exchanged (BUI-2027), and each user's
tool calls per UTC day (``count_call``), so every replica holds a user to one daily limit
(BUI-2212).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date
from typing import Self

from sqlalchemy import (
    Column,
    Connection,
    Engine,
    Float,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Table,
    Text,
    create_engine,
    delete,
    func,
    insert,
    inspect,
    select,
    text,
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
from sqlalchemy.sql.elements import ColumnElement

from lifescape.research_workspace import StoredResearchPacket
from lifescape.store import (
    OWNER_LOGIN_ENV,
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

# A GitHub login is at most 39 characters; the column leaves room for another identity provider.
OWNER_LENGTH = 255
OWNER_COLUMN = "owner"
# The owner of a row saved with no user (``user=None``).
NO_USER = ""
PACKETS_TABLE = "research_packets"
RUN_INDEXES_TABLE = "run_report_indexes"
# The rebuilt run index table is made under this name, then renamed over the old one.
RUN_INDEXES_REBUILD = "run_report_indexes_rebuild"


def _owner_column() -> Column[str]:
    return Column(OWNER_COLUMN, String(OWNER_LENGTH), nullable=False, server_default=text("''"))


def _run_indexes(name: str, metadata: MetaData) -> Table:
    return Table(
        name,
        metadata,
        _owner_column(),
        Column("run_id", String(16), nullable=False),
        Column("document", Text, nullable=False),
        PrimaryKeyConstraint(OWNER_COLUMN, "run_id"),
    )


METADATA = MetaData()
PACKETS = Table(
    PACKETS_TABLE,
    METADATA,
    Column("packet_id", String(12), primary_key=True),
    Column("document", Text, nullable=False),
    _owner_column(),
)
RUN_INDEXES = _run_indexes(RUN_INDEXES_TABLE, METADATA)
# One row per spent grant ID (``code:<jti>`` or ``refresh:<jti>``) until the grant expires.
SPENT_GRANTS = Table(
    "spent_oauth_grants",
    METADATA,
    Column("grant_id", String(128), primary_key=True),
    Column("expires_at", Float, nullable=False),
)
# One row per user and UTC day (ISO ``YYYY-MM-DD``): the tool calls that user made that day.
DAILY_CALLS = Table(
    "daily_tool_calls",
    METADATA,
    Column(OWNER_COLUMN, String(OWNER_LENGTH), nullable=False),
    Column("day", String(10), nullable=False),
    Column("calls", Integer, nullable=False),
    PrimaryKeyConstraint(OWNER_COLUMN, "day"),
)


class DatabaseStore:
    """Research packets and run report indexes in one SQL database."""

    def __init__(self, engine: Engine, owner: str | None = None) -> None:
        self._engine = engine
        self._owner = owner
        self._schema_ready = False

    @classmethod
    def from_url(cls, url: str, owner: str | None = None) -> Self:
        """Build the adapter without connecting; the first call connects.

        ``owner`` is the user who gets the rows saved before rows had an owner (``_migrate``)."""
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
        return cls(engine, owner)

    @property
    def label(self) -> str:
        return f"database store at {self._engine.url.render_as_string(hide_password=True)}"

    @contextmanager
    def _transaction(self) -> Iterator[Connection]:
        """One transaction; a driver failure becomes a ``StoreError`` naming this store."""
        try:
            with self._engine.begin() as connection:
                if not self._schema_ready:
                    self._migrate(connection)
                    METADATA.create_all(connection)
                yield connection
            self._schema_ready = True
        except (OperationalError, InterfaceError) as exc:
            raise StoreUnavailableError(f"{self.label} is unreachable: {_reason(exc)}") from exc
        except DBAPIError as exc:
            raise StoreError(f"{self.label} refused the request: {_reason(exc)}") from exc

    def _migrate(self, connection: Connection) -> None:
        """Give the record tables made before rows had an owner their ``owner`` column, and
        their rows to ``self._owner``. Only a table without the column is changed, so this
        assigns each row once. A second replica that migrates at the same time fails on the
        schema change and rolls back; its next request finds the tables migrated."""
        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
        legacy = [
            name
            for name in (PACKETS_TABLE, RUN_INDEXES_TABLE)
            if name in tables
            and OWNER_COLUMN not in {column["name"] for column in inspector.get_columns(name)}
        ]
        if not legacy:
            return
        held = sum(
            int(connection.execute(select(func.count()).select_from(text(name))).scalar_one())
            for name in legacy
        )
        if held and self._owner is None:
            raise StoreConfigError(
                f"the {self.label} holds {held} record(s) saved before records had an owner; "
                f"set {OWNER_LOGIN_ENV} to the GitHub login that owns them and restart"
            )
        owner = NO_USER if self._owner is None else self._owner
        if PACKETS_TABLE in legacy:
            connection.execute(
                text(
                    f"ALTER TABLE {PACKETS_TABLE} ADD COLUMN {OWNER_COLUMN} "
                    f"VARCHAR({OWNER_LENGTH}) NOT NULL DEFAULT ''"
                )
            )
            connection.execute(
                text(f"UPDATE {PACKETS_TABLE} SET {OWNER_COLUMN} = :owner"), {"owner": owner}
            )
        if RUN_INDEXES_TABLE in legacy:
            # The key changes from run_id to (owner, run_id): build the table again.
            rebuild = _run_indexes(RUN_INDEXES_REBUILD, MetaData())
            rebuild.create(connection)
            connection.execute(
                text(
                    f"INSERT INTO {RUN_INDEXES_REBUILD} ({OWNER_COLUMN}, run_id, document) "
                    f"SELECT :owner, run_id, document FROM {RUN_INDEXES_TABLE}"
                ),
                {"owner": owner},
            )
            connection.execute(text(f"DROP TABLE {RUN_INDEXES_TABLE}"))
            connection.execute(
                text(f"ALTER TABLE {RUN_INDEXES_REBUILD} RENAME TO {RUN_INDEXES_TABLE}")
            )

    @staticmethod
    def _owned_by(table: Table, user: str | None) -> ColumnElement[bool]:
        """The rows ``user`` owns; with no user, the rows saved with no user."""
        return table.c.owner == (NO_USER if user is None else user)

    def _document(
        self, connection: Connection, table: Table, key_name: str, key: str, user: str | None
    ) -> str | None:
        key_column = table.c[key_name]
        document = connection.execute(
            select(table.c.document).where(key_column == key, self._owned_by(table, user))
        ).scalar_one_or_none()
        return None if document is None else str(document)

    def _upsert(
        self, table: Table, key_name: str, key: str, document: str, user: str | None
    ) -> None:
        """Replace the user's row with this key, or insert it. A packet ID another user owns
        makes the insert fail on the primary key (``StoreError``): it is never replaced."""
        key_column = table.c[key_name]
        owner = NO_USER if user is None else user
        with self._transaction() as connection:
            updated = connection.execute(
                update(table)
                .where(key_column == key, self._owned_by(table, user))
                .values(document=document)
            )
            if updated.rowcount == 0:
                connection.execute(
                    insert(table).values(
                        {key_column.name: key, "document": document, OWNER_COLUMN: owner}
                    )
                )

    def load_packets(self, user: str | None = None) -> tuple[StoredResearchPacket, ...]:
        with self._transaction() as connection:
            rows = connection.execute(
                select(PACKETS.c.packet_id, PACKETS.c.document)
                .where(self._owned_by(PACKETS, user))
                .order_by(PACKETS.c.packet_id)
            ).all()
        return tuple(
            self._parse(StoredResearchPacket.model_validate_json, "research packet", key, doc)
            for key, doc in rows
        )

    def get_packet(self, packet_id: str, user: str | None = None) -> StoredResearchPacket | None:
        check_packet_id(packet_id)
        with self._transaction() as connection:
            document = self._document(connection, PACKETS, "packet_id", packet_id, user)
        if document is None:
            return None
        return self._parse(
            StoredResearchPacket.model_validate_json, "research packet", packet_id, document
        )

    def save_packet(self, stored: StoredResearchPacket, user: str | None = None) -> None:
        self._upsert(
            PACKETS, "packet_id", check_packet_id(stored.packet.id), stored.model_dump_json(), user
        )

    def get_run_index(self, run_id: str, user: str | None = None) -> RunReportIndex | None:
        check_run_id(run_id)
        with self._transaction() as connection:
            document = self._document(connection, RUN_INDEXES, "run_id", run_id, user)
        if document is None:
            return None
        return self._parse(
            RunReportIndex.model_validate_json, "report index of run", run_id, document
        )

    def save_run_index(self, index: RunReportIndex, user: str | None = None) -> None:
        self._upsert(
            RUN_INDEXES, "run_id", check_run_id(index.run_id), index.model_dump_json(), user
        )

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

    def count_call(self, user: str, day: date) -> int:
        """Count one tool call by ``user`` on the UTC ``day``; return the calls that day so far.

        The increment runs in the database, so calls on every replica add up. The first call of
        a user's day inserts its row and drops the rows of earlier days. Of two replicas that
        insert one user's first row at once, the primary key refuses one; it counts again on
        the row the other made.
        """
        stamp = day.isoformat()
        mine = (DAILY_CALLS.c.owner == user) & (DAILY_CALLS.c.day == stamp)
        try:
            return self._count(mine, user, stamp)
        except StoreError as exc:
            if not isinstance(exc.__cause__, IntegrityError):
                raise
        return self._count(mine, user, stamp)

    def _count(self, mine: ColumnElement[bool], user: str, stamp: str) -> int:
        with self._transaction() as connection:
            updated = connection.execute(
                update(DAILY_CALLS).where(mine).values(calls=DAILY_CALLS.c.calls + 1)
            )
            if updated.rowcount == 0:
                connection.execute(delete(DAILY_CALLS).where(DAILY_CALLS.c.day < stamp))
                connection.execute(insert(DAILY_CALLS).values(owner=user, day=stamp, calls=1))
                calls = 1
            else:
                calls = int(
                    connection.execute(select(DAILY_CALLS.c.calls).where(mine)).scalar_one()
                )
        return calls

    def _parse[T](self, parse: Callable[[str], T], kind: str, key: object, document: object) -> T:
        try:
            return parse(str(document))
        except ValueError as exc:
            raise ValueError(f"cannot load {kind} {key} from the {self.label}: {exc}") from exc


def _reason(exc: DBAPIError) -> str:
    """The driver's message only: SQLAlchemy's own text repeats the statement and parameters."""
    return f"{type(exc.orig).__name__}: {exc.orig}".strip()
