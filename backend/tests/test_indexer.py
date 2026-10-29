import logging
import threading

import pytest
from sqlalchemy import func, select
from stellar_sdk import scval

from soroban_indexer import db
from soroban_indexer.config import Config
from soroban_indexer.indexer import Indexer, event_to_row, retention_help
from soroban_indexer.rpc import RawEvent, RetentionWindowError, RpcError

from .conftest import FakeRpc
from .helpers import ALICE, BOB, OTHER, TOKEN, rpc_event


def cfg(**kw) -> Config:
    base = dict(
        poll_interval_seconds=0, backoff_initial_seconds=0, backoff_max_seconds=0, page_limit=3
    )
    base.update(kw)
    return Config(**base)


def count(session_factory) -> int:
    with session_factory() as s:
        return s.scalar(select(func.count()).select_from(db.Event))


def ids(session_factory) -> list[str]:
    with session_factory() as s:
        return list(s.scalars(select(db.Event.id).order_by(db.Event.id)))


def state(session_factory, key):
    with session_factory() as s:
        return db.get_state(s, key)


def events_at(*ledgers):
    return [rpc_event(lg, tx=i + 1) for i, lg in enumerate(ledgers)]


def test_event_to_row_decodes_everything():
    ev = RawEvent.from_rpc(rpc_event(10, value=scval.to_int128(2**70)))
    row = event_to_row(ev)
    assert row["topics"] == ["transfer", ALICE, BOB]
    assert row["topic0"] == "transfer"
    assert row["value"] == str(2**70)
    assert row["decode_ok"] is True
    assert row["topics_xdr"] == ev.topic_xdr


def test_event_to_row_with_garbage_never_raises():
    ev = RawEvent.from_rpc(rpc_event(10, topic_xdr=["!!bad"], value_xdr="also bad"))
    row = event_to_row(ev)
    assert row["decode_ok"] is False
    assert row["topic0"] is None
    assert row["topics"][0]["$undecodable"] is True
    assert row["value"]["xdr"] == "also bad"


def test_event_to_row_no_topics():
    row = event_to_row(RawEvent.from_rpc(rpc_event(10, topic_xdr=[])))
    assert row["topic0"] is None and row["topics"] == []


def test_first_run_uses_start_ledger(session_factory):
    rpc = FakeRpc(events_at(5, 10, 11), latest=20)
    result = Indexer(cfg(start_ledger=10), rpc, session_factory).poll_once()
    assert result.new_events == 2
    assert rpc.calls[0]["params"]["startLedger"] == 10
    assert not any(c["method"] == "getLatestLedger" for c in rpc.calls)
    assert state(session_factory, db.STATE_START_LEDGER) == "10"


def test_first_run_without_start_ledger_uses_latest(session_factory):
    rpc = FakeRpc(events_at(5, 20), latest=20)
    result = Indexer(cfg(), rpc, session_factory).poll_once()
    assert rpc.calls[0] == {"method": "getLatestLedger"}
    assert rpc.calls[1]["params"]["startLedger"] == 20
    assert result.new_events == 1


def test_paginates_until_short_page(session_factory):
    rpc = FakeRpc(events_at(*range(1, 8)), latest=50)  # 7 events, page size 3
    result = Indexer(cfg(start_ledger=1), rpc, session_factory).poll_once()
    assert result.new_events == 7
    assert result.pages == 3  # 3 + 3 + 1
    get_events = [c["params"] for c in rpc.calls if c["method"] == "getEvents"]
    assert "startLedger" in get_events[0]
    for p in get_events[1:]:
        assert "startLedger" not in p and "cursor" in p["pagination"]


def test_exact_multiple_of_page_size_needs_one_empty_page(session_factory):
    rpc = FakeRpc(events_at(1, 2, 3), latest=50)
    result = Indexer(cfg(start_ledger=1), rpc, session_factory).poll_once()
    assert result.new_events == 3 and result.pages == 2


def test_resume_from_cursor_without_duplicates(session_factory):
    rpc = FakeRpc(events_at(1, 2, 3, 4), latest=10)
    Indexer(cfg(start_ledger=1), rpc, session_factory).poll_once()
    stored_cursor = state(session_factory, db.STATE_CURSOR)
    assert count(session_factory) == 4

    # "Restart": a brand-new Indexer with a fresh RPC client and the same DB.
    rpc2 = FakeRpc(rpc.events, latest=10)
    rpc2.add(rpc_event(11, tx=1), rpc_event(12, tx=1))
    rpc2.latest = 12
    result = Indexer(cfg(start_ledger=1), rpc2, session_factory).poll_once()
    first = rpc2.calls[0]["params"]
    assert first["pagination"]["cursor"] == stored_cursor
    assert "startLedger" not in first
    assert result.new_events == 2
    assert count(session_factory) == 6
    assert len(ids(session_factory)) == len(set(ids(session_factory)))


def test_restart_before_first_page_keeps_start_ledger(session_factory):
    rpc = FakeRpc(events_at(5), latest=100)
    rpc.fail_next.append(RpcError("down"))
    with pytest.raises(RpcError):
        Indexer(cfg(), rpc, session_factory).poll_once()
    assert state(session_factory, db.STATE_START_LEDGER) == "100"
    rpc.latest = 200  # chain moved on; we must not jump to 200
    Indexer(cfg(), rpc, session_factory).poll_once()
    assert rpc.calls[-1]["params"]["startLedger"] == 100


def test_reinserting_same_events_is_idempotent(session_factory):
    evs = events_at(1, 2)
    with session_factory() as s:
        rows = [event_to_row(RawEvent.from_rpc(e)) for e in evs]
        assert db.insert_events_ignore_duplicates(s, rows) == 2
        assert db.insert_events_ignore_duplicates(s, rows) == 0
        assert db.insert_events_ignore_duplicates(s, []) == 0
        s.commit()
    assert count(session_factory) == 2


def test_large_batch_insert(session_factory):
    rows = [event_to_row(RawEvent.from_rpc(rpc_event(1, tx=i + 1))) for i in range(1200)]
    with session_factory() as s:
        assert db.insert_events_ignore_duplicates(s, rows) == 1200
        s.commit()


def test_failure_mid_pagination_keeps_committed_pages(session_factory):
    rpc = FakeRpc(events_at(*range(1, 8)), latest=50)
    ix = Indexer(cfg(start_ledger=1), rpc, session_factory)

    real = rpc.get_events
    calls = {"n": 0}

    def flaky(**kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RpcError("transient")
        return real(**kw)

    rpc.get_events = flaky
    with pytest.raises(RpcError):
        ix.poll_once()
    assert count(session_factory) == 3  # first page committed with its cursor
    ix.poll_once()
    assert count(session_factory) == 7
    assert len(set(ids(session_factory))) == 7


def test_contract_filter_is_passed_through(session_factory):
    rpc = FakeRpc([rpc_event(1, contract=TOKEN), rpc_event(2, contract=OTHER)], latest=5)
    Indexer(cfg(start_ledger=1, contract_ids=(OTHER,)), rpc, session_factory).poll_once()
    assert rpc.calls[0]["params"]["filters"] == [{"contractIds": [OTHER]}]
    with session_factory() as s:
        assert [e.contract_id for e in s.scalars(select(db.Event))] == [OTHER]


def test_stalled_cursor_does_not_loop_forever(session_factory):
    rpc = FakeRpc(events_at(1, 2, 3), latest=5)
    real = rpc.get_events

    def same_cursor(**kw):
        page = real(**kw)
        page.cursor = kw.get("cursor") or page.cursor
        return page

    rpc.get_events = same_cursor
    ix = Indexer(cfg(start_ledger=1), rpc, session_factory)
    ix.poll_once()
    result = ix.poll_once()  # full page whose cursor equals the request cursor
    assert result.pages == 1


def test_records_progress_state(session_factory):
    rpc = FakeRpc(events_at(1), latest=42)
    Indexer(cfg(start_ledger=1), rpc, session_factory).poll_once()
    assert state(session_factory, db.STATE_LAST_LEDGER) == "42"
    assert state(session_factory, db.STATE_LAST_POLL) is not None


def test_retention_error_gets_actionable_message(session_factory):
    rpc = FakeRpc([], latest=5000)
    rpc.fail_next.append(RetentionWindowError("x", 1000, 5000))
    with pytest.raises(RetentionWindowError) as exc:
        Indexer(cfg(start_ledger=10), rpc, session_factory).poll_once()
    msg = str(exc.value)
    assert "START_LEDGER=10" in msg
    assert "Set START_LEDGER to 1000 or later" in msg
    assert "1000..5000" in msg


def test_retention_help_variants():
    err = RetentionWindowError("x", None, None)
    assert "reset-cursor" in retention_help(err, using_cursor=True, start=None)
    assert "no longer has that history" in retention_help(err, using_cursor=False, start=5)
    assert "more recent ledger" in retention_help(err, using_cursor=False, start=5)


def test_run_forever_survives_errors_and_backs_off(session_factory, caplog):
    rpc = FakeRpc(events_at(1, 2), latest=10)
    rpc.fail_next = [RpcError("boom"), RetentionWindowError("old", 1, 10), RuntimeError("bug")]
    polls = []
    ix = Indexer(cfg(start_ledger=1, backoff_max_seconds=0.05), rpc, session_factory)
    with caplog.at_level(logging.INFO, logger="soroban_indexer.indexer"):
        ix.run_forever(max_polls=2, on_poll=polls.append)
    assert len(polls) == 2
    assert polls[0].new_events == 2
    assert count(session_factory) == 2
    text = caplog.text
    assert "RPC error, retrying" in text
    assert "retention window" in text
    assert "unexpected error" in text
    assert "indexer stopped" in text


def test_run_forever_stops_on_event(session_factory):
    rpc = FakeRpc([], latest=10)
    stop = threading.Event()
    ix = Indexer(cfg(start_ledger=1, poll_interval_seconds=10), rpc, session_factory)
    t = threading.Thread(target=ix.run_forever, kwargs={"stop": stop})
    t.start()
    stop.set()
    t.join(timeout=5)
    assert not t.is_alive()


def test_run_forever_resets_backoff_after_success(session_factory, monkeypatch):
    rpc = FakeRpc([], latest=10)
    rpc.fail_next = [RpcError("a"), RpcError("b")]
    waits = []

    class RecordingEvent(threading.Event):
        def wait(self, timeout=None):
            waits.append(timeout)
            return False

    ix = Indexer(
        cfg(
            start_ledger=1,
            backoff_initial_seconds=1,
            backoff_max_seconds=3,
            poll_interval_seconds=7,
        ),
        rpc,
        session_factory,
    )
    ix.run_forever(stop=RecordingEvent(), max_polls=2)
    assert waits == [1, 2, 7]


def test_poll_once_stops_between_pages_when_asked(session_factory):
    rpc = FakeRpc(events_at(*range(1, 11)), latest=50)  # 10 events, page size 3
    stop = threading.Event()
    stop.set()
    ix = Indexer(cfg(start_ledger=1), rpc, session_factory)
    first = ix.poll_once(stop)
    assert first.pages == 1 and count(session_factory) == 3
    rest = ix.poll_once()
    assert first.new_events + rest.new_events == 10
    assert count(session_factory) == 10
