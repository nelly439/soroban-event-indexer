import json

import httpx
import pytest

from soroban_indexer.rpc import (
    RawEvent,
    RetentionWindowError,
    RpcClient,
    RpcError,
    build_filters,
    build_get_events_params,
)

from .helpers import OTHER, TOKEN, rpc_event


class Recorder:
    """httpx transport that records requests and replies with canned responses."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        resp = self.responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        if isinstance(resp, httpx.Response):
            return resp
        return httpx.Response(200, json=resp)


def client(rec: Recorder) -> RpcClient:
    return RpcClient("http://rpc.test", transport=httpx.MockTransport(rec))


def ok(result):
    return {"jsonrpc": "2.0", "id": 1, "result": result}


def test_get_latest_ledger_request_shape():
    rec = Recorder(ok({"id": "abc", "protocolVersion": 23, "sequence": 1234}))
    with client(rec) as c:
        assert c.get_latest_ledger() == 1234
    req = rec.requests[0]
    assert req["jsonrpc"] == "2.0"
    assert req["method"] == "getLatestLedger"
    assert isinstance(req["id"], int)
    assert "params" not in req


def test_get_events_with_start_ledger():
    ev = rpc_event(100)
    rec = Recorder(ok({"events": [ev], "cursor": "c1", "latestLedger": 120, "oldestLedger": 5}))
    page = client(rec).get_events(start_ledger=100, limit=10, contract_ids=(TOKEN,))
    params = rec.requests[0]["params"]
    assert rec.requests[0]["method"] == "getEvents"
    assert params == {
        "startLedger": 100,
        "filters": [{"contractIds": [TOKEN]}],
        "pagination": {"limit": 10},
    }
    assert page.cursor == "c1"
    assert page.latest_ledger == 120
    assert page.oldest_ledger == 5
    assert page.events[0].id == ev["id"]
    assert page.events[0].contract_id == TOKEN
    assert page.events[0].topic_xdr == ev["topic"]


def test_get_events_with_cursor_never_sends_start_ledger():
    rec = Recorder(ok({"events": [], "cursor": "c2", "latestLedger": 130}))
    client(rec).get_events(cursor="c1", limit=5)
    params = rec.requests[0]["params"]
    assert "startLedger" not in params
    assert params["pagination"] == {"cursor": "c1", "limit": 5}
    assert params["filters"] == [{}]


@pytest.mark.parametrize(("start", "cursor"), [(None, None), (5, "abc")])
def test_start_ledger_and_cursor_are_mutually_exclusive(start, cursor):
    with pytest.raises(ValueError, match="exactly one"):
        build_get_events_params(start_ledger=start, cursor=cursor, limit=1)


def test_filters_chunk_contract_ids_by_five():
    ids = [TOKEN, OTHER] * 3 + [TOKEN]
    filters = build_filters(ids)
    assert [len(f["contractIds"]) for f in filters] == [5, 2]
    assert build_filters([]) == [{}]


def test_cursor_falls_back_to_last_event_id_for_old_rpc():
    ev = rpc_event(100)
    rec = Recorder(ok({"events": [ev], "latestLedger": 120}))
    assert client(rec).get_events(start_ledger=1).cursor == ev["id"]


def test_empty_page_without_cursor():
    rec = Recorder(ok({"events": [], "latestLedger": 120}))
    page = client(rec).get_events(start_ledger=1)
    assert page.cursor is None and page.events == []


def test_legacy_value_object_shape():
    ev = rpc_event(100)
    ev["value"] = {"xdr": ev["value"]}
    assert RawEvent.from_rpc(ev).value_xdr == ev["value"]["xdr"]


def test_missing_contract_id_becomes_none():
    ev = rpc_event(100, contract=None, type="system")
    raw = RawEvent.from_rpc(ev)
    assert raw.contract_id is None and raw.type == "system"


def test_jsonrpc_error():
    rec = Recorder({"jsonrpc": "2.0", "id": 1, "error": {"code": -32602, "message": "bad filter"}})
    with pytest.raises(RpcError, match="bad filter") as exc:
        client(rec).get_events(start_ledger=1)
    assert exc.value.code == -32602


def test_retention_window_error_is_detected():
    msg = (
        "startLedger must be between the oldest ledger: 1000 and the latest ledger: 2000 "
        "for this processing window"
    )
    rec = Recorder({"jsonrpc": "2.0", "id": 1, "error": {"code": -32600, "message": msg}})
    with pytest.raises(RetentionWindowError) as exc:
        client(rec).get_events(start_ledger=1)
    assert exc.value.oldest_ledger == 1000
    assert exc.value.latest_ledger == 2000


def test_string_error_payload():
    rec = Recorder({"jsonrpc": "2.0", "id": 1, "error": "boom"})
    with pytest.raises(RpcError, match="boom"):
        client(rec).get_latest_ledger()


@pytest.mark.parametrize(
    ("response", "match"),
    [
        (httpx.Response(500, text="oops"), "HTTP 500"),
        (httpx.Response(429, text="slow down"), "rate limited"),
        (httpx.Response(200, text="<html>"), "not JSON"),
        (httpx.Response(200, json=[1, 2]), "unexpected response shape"),
        (httpx.Response(200, json={"jsonrpc": "2.0", "id": 1}), "no result"),
        (httpx.ConnectError("refused"), "transport error"),
        (httpx.ReadTimeout("slow"), "transport error"),
    ],
)
def test_transport_and_protocol_errors(response, match):
    with pytest.raises(RpcError, match=match):
        client(Recorder(response)).get_latest_ledger()


def test_rate_limit_has_code():
    with pytest.raises(RpcError) as exc:
        client(Recorder(httpx.Response(429))).get_latest_ledger()
    assert exc.value.code == 429


def test_latest_ledger_missing_sequence():
    with pytest.raises(RpcError, match="sequence"):
        client(Recorder(ok({}))).get_latest_ledger()


def test_malformed_event():
    with pytest.raises(RpcError, match="malformed event"):
        client(Recorder(ok({"events": [{"type": "contract"}]}))).get_events(start_ledger=1)


def test_request_ids_increment():
    rec = Recorder(ok({"sequence": 1}), ok({"sequence": 2}))
    c = client(rec)
    c.get_latest_ledger()
    c.get_latest_ledger()
    assert rec.requests[1]["id"] == rec.requests[0]["id"] + 1
