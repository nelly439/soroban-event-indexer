"""The polling loop that moves events from Stellar RPC into the database.

Correctness model
-----------------
* The RPC cursor is persisted in ``indexer_state`` **in the same transaction**
  as the events of the page it follows. Either both are committed or neither
  is, so a crash can never skip events (gap) or advance past uncommitted ones.
* Event ids are primary keys and inserts use ``ON CONFLICT DO NOTHING``, so
  re-reading a page after a crash cannot create duplicates.
* The first run starts at ``START_LEDGER`` or, if unset, at the latest ledger.
  The chosen start ledger is persisted so that a restart before the first
  page lands does not silently jump forward.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from . import db
from .config import Config
from .decoder import decode_or_marker, topic_key
from .rpc import EventsPage, RawEvent, RetentionWindowError, RpcClient, RpcError

log = logging.getLogger("soroban_indexer.indexer")


def event_to_row(ev: RawEvent) -> dict[str, Any]:
    """Decode a raw RPC event into a row for the ``events`` table.

    Decoding failures are recorded with an undecodable marker and
    ``decode_ok=False``; they never raise.
    """
    topics: list[Any] = []
    ok = True
    for t in ev.topic_xdr:
        value, t_ok = decode_or_marker(t)
        topics.append(value)
        ok = ok and t_ok
    value, v_ok = decode_or_marker(ev.value_xdr)
    topic0_ok = bool(ev.topic_xdr) and not (
        isinstance(topics[0], dict) and topics[0].get("$undecodable")
    )
    return {
        "id": ev.id,
        "ledger": ev.ledger,
        "ledger_closed_at": ev.ledger_closed_at,
        "contract_id": ev.contract_id,
        "type": ev.type,
        "tx_hash": ev.tx_hash,
        "in_successful_contract_call": ev.in_successful_contract_call,
        "topic0": topic_key(topics[0]) if topic0_ok else None,
        "topics": topics,
        "value": value,
        "topics_xdr": list(ev.topic_xdr),
        "value_xdr": ev.value_xdr,
        "decode_ok": ok and v_ok,
    }


@dataclass
class PollResult:
    """Outcome of one :meth:`Indexer.poll_once` call."""

    new_events: int
    pages: int
    cursor: str | None
    latest_ledger: int | None


def retention_help(err: RetentionWindowError, using_cursor: bool, start: int | None) -> str:
    """Return a multi-line, actionable explanation of a retention-window error."""
    window = (
        f"the node currently serves ledgers {err.oldest_ledger}..{err.latest_ledger}"
        if err.oldest_ledger is not None
        else "the node no longer has that history"
    )
    if using_cursor:
        what = (
            "The stored cursor points at ledgers the RPC node has already pruned "
            "(the indexer was probably stopped for longer than the node's retention window)"
        )
        fix = (
            "Run `soroban-indexer reset-cursor` and set START_LEDGER to a ledger inside the "
            "window (or leave it unset to start at the latest ledger). Events in the pruned "
            "range cannot be recovered from this node; use an RPC with longer retention if you "
            "need them."
        )
    else:
        what = f"START_LEDGER={start} is older than the oldest ledger the RPC node keeps"
        fix = (
            f"Set START_LEDGER to {err.oldest_ledger} or later"
            if err.oldest_ledger is not None
            else "Set START_LEDGER to a more recent ledger"
        )
        fix += (
            ", or unset it to start at the latest ledger, then run "
            "`soroban-indexer reset-cursor`. RPC nodes only keep a rolling window "
            "of history (HISTORY_RETENTION_WINDOW on the node)."
        )
    return f"{what}; {window}. How to fix: {fix}"


class Indexer:
    """Polls ``getEvents`` and stores decoded events."""

    def __init__(
        self,
        config: Config,
        rpc: RpcClient,
        session_factory: sessionmaker[Session],
    ) -> None:
        self.config = config
        self.rpc = rpc
        self.session_factory = session_factory

    # -- position -------------------------------------------------------------

    def _resolve_position(self, session: Session) -> tuple[str | None, int | None]:
        """Return ``(cursor, start_ledger)``; exactly one is not ``None``."""
        cursor = db.get_state(session, db.STATE_CURSOR)
        if cursor:
            return cursor, None
        stored_start = db.get_state(session, db.STATE_START_LEDGER)
        if stored_start:
            return None, int(stored_start)
        if self.config.start_ledger is not None:
            start = self.config.start_ledger
            log.info("first run: starting at START_LEDGER=%s", start)
        else:
            start = self.rpc.get_latest_ledger()
            log.info("first run: START_LEDGER unset, starting at latest ledger %s", start)
        db.set_state(session, db.STATE_START_LEDGER, str(start))
        session.commit()
        return None, start

    # -- one poll ---------------------------------------------------------------

    def _store_page(self, session: Session, page: EventsPage) -> int:
        """Insert a page and advance the cursor atomically. Returns rows inserted."""
        rows = [event_to_row(e) for e in page.events]
        inserted = db.insert_events_ignore_duplicates(session, rows)
        if page.cursor:
            db.set_state(session, db.STATE_CURSOR, page.cursor)
        if page.latest_ledger is not None:
            db.set_state(session, db.STATE_LAST_LEDGER, str(page.latest_ledger))
        db.set_state(session, db.STATE_LAST_POLL, datetime.now(UTC).isoformat())
        session.commit()
        return inserted

    def poll_once(self, stop: threading.Event | None = None) -> PollResult:
        """Fetch pages until a short page is returned, storing each one.

        If ``stop`` is set, returns after the current page (every page is
        committed, so the next run resumes exactly there). Raises
        :class:`~soroban_indexer.rpc.RpcError` on RPC failure; whatever was
        committed before the failure is kept and resumes cleanly.
        """
        new_events = 0
        pages = 0
        latest: int | None = None
        with self.session_factory() as session:
            cursor, start = self._resolve_position(session)
            while True:
                try:
                    page = self.rpc.get_events(
                        start_ledger=start,
                        cursor=cursor,
                        limit=self.config.page_limit,
                        contract_ids=self.config.contract_ids,
                    )
                except RetentionWindowError as err:
                    err.args = (retention_help(err, cursor is not None, start),)
                    raise
                pages += 1
                new_events += self._store_page(session, page)
                latest = page.latest_ledger if page.latest_ledger is not None else latest
                short = len(page.events) < self.config.page_limit
                stalled = page.cursor is None or page.cursor == cursor
                if page.cursor:
                    cursor, start = page.cursor, None
                if short or stalled or (stop is not None and stop.is_set()):
                    break
        return PollResult(new_events=new_events, pages=pages, cursor=cursor, latest_ledger=latest)

    # -- loop -------------------------------------------------------------------

    def run_forever(
        self,
        stop: threading.Event | None = None,
        max_polls: int | None = None,
        on_poll: Callable[[PollResult], None] | None = None,
    ) -> None:
        """Poll until ``stop`` is set (or ``max_polls`` successful polls happen).

        Never raises: RPC errors and unexpected exceptions are logged and
        retried with exponential backoff capped at ``BACKOFF_MAX_SECONDS``.
        """
        stop = stop or threading.Event()
        backoff = self.config.backoff_initial_seconds
        polls = 0
        log.info(
            "indexer started: rpc=%s contracts=%s page_limit=%s interval=%ss",
            self.config.rpc_url,
            ",".join(self.config.contract_ids) or "<all>",
            self.config.page_limit,
            self.config.poll_interval_seconds,
        )
        while not stop.is_set():
            try:
                result = self.poll_once(stop)
            except RetentionWindowError as err:
                log.error("RPC retention window error: %s", err)
                delay = backoff
            except RpcError as err:
                log.warning("RPC error, retrying in %.1fs: %s", backoff, err)
                delay = backoff
            except Exception:  # noqa: BLE001 - the loop must never die
                log.exception("unexpected error in indexer loop, retrying in %.1fs", backoff)
                delay = backoff
            else:
                if result.new_events or result.pages > 1:
                    log.info(
                        "indexed %d new events in %d page(s); latest ledger %s",
                        result.new_events,
                        result.pages,
                        result.latest_ledger,
                    )
                if on_poll is not None:
                    on_poll(result)
                polls += 1
                backoff = self.config.backoff_initial_seconds
                if max_polls is not None and polls >= max_polls:
                    break
                stop.wait(self.config.poll_interval_seconds)
                continue
            stop.wait(delay)
            backoff = min(max(backoff * 2, 0.1), self.config.backoff_max_seconds)
        log.info("indexer stopped")
