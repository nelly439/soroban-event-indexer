"""Database schema and data-access helpers (SQLAlchemy 2.0).

Two tables:

* ``events`` - one row per contract event; the RPC event id is the primary key,
  which makes re-inserting the same event a no-op.
* ``indexer_state`` - a small key/value table holding the RPC cursor and the
  ledger the indexer started from.

Works on SQLite (default) and PostgreSQL.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    create_engine,
    event,
    func,
    select,
)
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

STATE_CURSOR = "cursor"
STATE_START_LEDGER = "start_ledger"
STATE_LAST_LEDGER = "last_seen_ledger"
STATE_LAST_POLL = "last_poll_at"

#: Rows per INSERT statement.
INSERT_BATCH_SIZE = 500


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


class Event(Base):
    """A decoded Soroban contract event."""

    __tablename__ = "events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ledger: Mapped[int] = mapped_column(Integer, nullable=False)
    ledger_closed_at: Mapped[str | None] = mapped_column(String(40))
    contract_id: Mapped[str | None] = mapped_column(String(64))
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    tx_hash: Mapped[str | None] = mapped_column(String(64))
    in_successful_contract_call: Mapped[bool | None] = mapped_column(Boolean)
    topic0: Mapped[str | None] = mapped_column(Text)
    topics: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    value: Mapped[Any] = mapped_column(JSON, nullable=True)
    topics_xdr: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    value_xdr: Mapped[str] = mapped_column(Text, nullable=False)
    decode_ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    indexed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    __table_args__ = (
        Index("ix_events_ledger", "ledger"),
        Index("ix_events_contract_ledger", "contract_id", "ledger"),
        Index("ix_events_topic0", "topic0"),
    )

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the API."""
        return {
            "id": self.id,
            "ledger": self.ledger,
            "ledger_closed_at": self.ledger_closed_at,
            "contract_id": self.contract_id,
            "type": self.type,
            "tx_hash": self.tx_hash,
            "in_successful_contract_call": self.in_successful_contract_call,
            "topics": self.topics,
            "value": self.value,
            "topics_xdr": self.topics_xdr,
            "value_xdr": self.value_xdr,
            "decode_ok": self.decode_ok,
        }


class IndexerState(Base):
    """Key/value store for indexer progress (cursor etc.)."""

    __tablename__ = "indexer_state"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


def make_engine(database_url: str) -> Engine:
    """Create an engine. For SQLite, enables WAL so the API can read while indexing."""
    kwargs: dict[str, Any] = {"pool_pre_ping": True}
    if database_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    engine = create_engine(database_url, **kwargs)
    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn: Any, _record: Any) -> None:
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.close()

    return engine


def init_db(engine: Engine) -> None:
    """Create tables if they do not exist."""
    Base.metadata.create_all(engine)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Return a session factory bound to ``engine``."""
    return sessionmaker(bind=engine, expire_on_commit=False)


# --- indexer state -----------------------------------------------------------


def get_state(session: Session, key: str) -> str | None:
    """Return the stored value for ``key`` or ``None``."""
    row = session.get(IndexerState, key)
    return row.value if row else None


def set_state(session: Session, key: str, value: str) -> None:
    """Insert or update a state key (caller commits)."""
    row = session.get(IndexerState, key)
    if row is None:
        session.add(IndexerState(key=key, value=value))
    else:
        row.value = value


def get_all_state(session: Session) -> dict[str, str]:
    """Return every state key/value pair."""
    return {r.key: r.value for r in session.scalars(select(IndexerState))}


# --- events --------------------------------------------------------------------


def insert_events_ignore_duplicates(session: Session, rows: Sequence[dict[str, Any]]) -> int:
    """Insert event rows, silently skipping ids that already exist.

    Returns the number of rows actually inserted. Uses ``ON CONFLICT DO
    NOTHING`` on SQLite and PostgreSQL; other dialects fall back to a
    select-then-insert.
    """
    if not rows:
        return 0
    dialect = session.get_bind().dialect.name
    if dialect in ("sqlite", "postgresql"):
        insert_fn = sqlite.insert if dialect == "sqlite" else postgresql.insert
        inserted = 0
        # Batch to stay well under SQLite's bound-parameter limit.
        for batch in chunked(rows, INSERT_BATCH_SIZE):
            stmt = insert_fn(Event).values(batch).on_conflict_do_nothing(index_elements=["id"])
            inserted += len(session.execute(stmt.returning(Event.id)).all())
        return inserted
    ids = [r["id"] for r in rows]  # pragma: no cover - other dialects
    existing = set(session.scalars(select(Event.id).where(Event.id.in_(ids))))  # pragma: no cover
    new = [Event(**r) for r in rows if r["id"] not in existing]  # pragma: no cover
    session.add_all(new)  # pragma: no cover
    return len(new)  # pragma: no cover


@dataclass
class EventQuery:
    """Filters accepted by :func:`query_events`."""

    contract_id: str | None = None
    type: str | None = None
    topic0: str | None = None
    from_ledger: int | None = None
    to_ledger: int | None = None
    limit: int = 50
    offset: int = 0


def query_events(session: Session, q: EventQuery) -> tuple[list[Event], int]:
    """Return ``(page, total)`` for the filters in ``q``, newest first."""
    conditions = []
    if q.contract_id:
        conditions.append(Event.contract_id == q.contract_id)
    if q.type:
        conditions.append(Event.type == q.type)
    if q.topic0 is not None:
        conditions.append(Event.topic0 == q.topic0)
    if q.from_ledger is not None:
        conditions.append(Event.ledger >= q.from_ledger)
    if q.to_ledger is not None:
        conditions.append(Event.ledger <= q.to_ledger)

    total = session.scalar(select(func.count()).select_from(Event).where(*conditions)) or 0
    stmt = (
        select(Event)
        .where(*conditions)
        .order_by(Event.ledger.desc(), Event.id.desc())
        .limit(q.limit)
        .offset(q.offset)
    )
    return list(session.scalars(stmt)), int(total)


def list_contracts(session: Session) -> list[dict[str, Any]]:
    """Per-contract aggregates: event count and first/last ledger, busiest first."""
    stmt = (
        select(
            Event.contract_id,
            func.count().label("event_count"),
            func.min(Event.ledger).label("first_ledger"),
            func.max(Event.ledger).label("last_ledger"),
        )
        .where(Event.contract_id.is_not(None))
        .group_by(Event.contract_id)
        .order_by(func.count().desc(), Event.contract_id)
    )
    return [
        {
            "contract_id": r.contract_id,
            "event_count": int(r.event_count),
            "first_ledger": int(r.first_ledger),
            "last_ledger": int(r.last_ledger),
        }
        for r in session.execute(stmt)
    ]


def event_stats(session: Session) -> dict[str, Any]:
    """Global counters used by ``GET /stats``."""
    total, contracts, min_l, max_l = session.execute(
        select(
            func.count(),
            func.count(func.distinct(Event.contract_id)),
            func.min(Event.ledger),
            func.max(Event.ledger),
        ).select_from(Event)
    ).one()
    failed = session.scalar(
        select(func.count()).select_from(Event).where(Event.decode_ok.is_(False))
    )
    by_type = {
        t: int(c) for t, c in session.execute(select(Event.type, func.count()).group_by(Event.type))
    }
    return {
        "total_events": int(total),
        "contracts": int(contracts),
        "min_ledger": min_l,
        "max_ledger": max_l,
        "undecodable_events": int(failed or 0),
        "events_by_type": by_type,
    }


def chunked(items: Iterable[Any], size: int) -> Iterable[list[Any]]:
    """Yield lists of at most ``size`` items."""
    batch: list[Any] = []
    for item in items:
        batch.append(item)
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch
