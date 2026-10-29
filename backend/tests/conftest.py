from __future__ import annotations

import os
from typing import Any

import pytest

from soroban_indexer import db
from soroban_indexer.rpc import EventsPage, RawEvent, RpcError, build_get_events_params


class FakeRpc:
    """In-memory stand-in for RpcClient with RPC-like cursor semantics.

    ``cursor`` = id of the last event returned; when a page is short the cursor
    moves to the end of the scanned range, like Stellar RPC does.
    """

    def __init__(self, events: list[dict[str, Any]] | None = None, latest: int = 1000) -> None:
        self.events = sorted(events or [], key=lambda e: e["id"])
        self.latest = latest
        self.calls: list[dict[str, Any]] = []
        self.fail_next: list[Exception] = []

    def add(self, *events: dict[str, Any]) -> None:
        self.events = sorted([*self.events, *events], key=lambda e: e["id"])

    def get_latest_ledger(self) -> int:
        self.calls.append({"method": "getLatestLedger"})
        return self.latest

    def get_events(self, *, start_ledger=None, cursor=None, limit=100, contract_ids=()):
        params = build_get_events_params(
            start_ledger=start_ledger, cursor=cursor, limit=limit, contract_ids=contract_ids
        )
        self.calls.append({"method": "getEvents", "params": params})
        if self.fail_next:
            raise self.fail_next.pop(0)
        matching = [e for e in self.events if not contract_ids or e["contractId"] in contract_ids]
        if cursor is not None:
            matching = [e for e in matching if e["id"] > cursor]
        else:
            matching = [e for e in matching if e["ledger"] >= start_ledger]
        page = matching[:limit]
        if len(page) == limit:
            next_cursor = page[-1]["id"]
        else:
            next_cursor = f"{(self.latest + 1) << 32:019d}-0000000000"
        return EventsPage(
            events=[RawEvent.from_rpc(e) for e in page],
            cursor=next_cursor,
            latest_ledger=self.latest,
        )


@pytest.fixture
def engine(tmp_path):
    """SQLite per test, or TEST_DATABASE_URL (e.g. PostgreSQL) wiped per test."""
    url = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 'test.db'}"
    eng = db.make_engine(url)
    db.Base.metadata.drop_all(eng)
    db.init_db(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session_factory(engine):
    return db.make_session_factory(engine)


__all__ = ["FakeRpc", "RpcError"]
