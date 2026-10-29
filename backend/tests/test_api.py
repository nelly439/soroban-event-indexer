import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from stellar_sdk import scval

from soroban_indexer import db
from soroban_indexer.api import create_app
from soroban_indexer.config import Config
from soroban_indexer.indexer import event_to_row
from soroban_indexer.rpc import RawEvent

from .helpers import ALICE, BOB, OTHER, TOKEN, rpc_event

SEED = [
    rpc_event(100, tx=1, contract=TOKEN),
    rpc_event(101, tx=1, contract=TOKEN, value=scval.to_int128(2**64)),
    rpc_event(102, tx=1, contract=OTHER, topics=[scval.to_symbol("mint"), scval.to_address(ALICE)]),
    rpc_event(103, tx=1, contract=OTHER, topics=[scval.to_uint32(7)], value=scval.to_void()),
    rpc_event(103, tx=2, contract=None, type="system", topics=[scval.to_symbol("core")]),
    rpc_event(104, tx=1, contract=TOKEN, topic_xdr=["garbage"], value_xdr="garbage"),
]


@pytest.fixture
def client(engine, session_factory):
    with session_factory() as s:
        db.insert_events_ignore_duplicates(s, [event_to_row(RawEvent.from_rpc(e)) for e in SEED])
        db.set_state(s, db.STATE_CURSOR, "cursor-1")
        db.set_state(s, db.STATE_START_LEDGER, "100")
        db.set_state(s, db.STATE_LAST_LEDGER, "110")
        s.commit()
    app = create_app(Config(cors_origins=("http://dash.test",)), engine=engine)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def ids_of(resp):
    return [e["id"] for e in resp.json()["items"]]


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "database": "ok", "version": "0.1.0"}


def test_health_reports_db_failure(engine):
    app = create_app(Config(), engine=engine)
    with TestClient(app) as c:
        engine.dispose()
        import sqlalchemy

        original = sqlalchemy.orm.Session.execute

        def boom(self, *a, **k):
            raise RuntimeError("db down")

        sqlalchemy.orm.Session.execute = boom
        try:
            r = c.get("/health")
        finally:
            sqlalchemy.orm.Session.execute = original
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "unavailable"


def test_events_newest_first_and_total(client):
    r = client.get("/events")
    body = r.json()
    assert r.status_code == 200
    assert body["total"] == len(SEED)
    assert body["limit"] == 50 and body["offset"] == 0
    ledgers = [e["ledger"] for e in body["items"]]
    assert ledgers == sorted(ledgers, reverse=True)
    # Same ledger: higher id first.
    same = [e["id"] for e in body["items"] if e["ledger"] == 103]
    assert same == sorted(same, reverse=True)


def test_event_shape_and_decoding(client):
    ev = client.get(f"/events/{SEED[1]['id']}").json()
    assert ev["topics"] == ["transfer", ALICE, BOB]
    assert ev["value"] == "18446744073709551616"
    assert ev["contract_id"] == TOKEN
    assert ev["type"] == "contract"
    assert ev["decode_ok"] is True
    assert ev["value_xdr"] == SEED[1]["value"]
    assert ev["topics_xdr"] == SEED[1]["topic"]
    assert ev["tx_hash"] == SEED[1]["txHash"]
    assert ev["ledger_closed_at"] == "2026-10-29T12:00:00Z"
    assert ev["in_successful_contract_call"] is True


def test_undecodable_event_is_served_with_marker(client):
    ev = client.get(f"/events/{SEED[5]['id']}").json()
    assert ev["decode_ok"] is False
    assert ev["value"]["$undecodable"] is True
    assert ev["value"]["xdr"] == "garbage"


def test_get_event_404(client):
    r = client.get("/events/does-not-exist")
    assert r.status_code == 404
    assert r.json() == {
        "error": {
            "code": "not_found",
            "message": "event 'does-not-exist' not found",
            "details": None,
        }
    }


def test_filter_contract_id(client):
    r = client.get("/events", params={"contract_id": OTHER})
    assert set(ids_of(r)) == {SEED[2]["id"], SEED[3]["id"]}
    assert r.json()["total"] == 2


def test_filter_type(client):
    r = client.get("/events", params={"type": "system"})
    assert ids_of(r) == [SEED[4]["id"]]


def test_filter_type_invalid(client):
    r = client.get("/events", params={"type": "bogus"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"


def test_filter_topic0_symbol(client):
    r = client.get("/events", params={"topic0": "transfer"})
    assert set(ids_of(r)) == {SEED[0]["id"], SEED[1]["id"]}


def test_filter_topic0_non_string(client):
    r = client.get("/events", params={"topic0": "7"})
    assert ids_of(r) == [SEED[3]["id"]]


def test_filter_ledger_range(client):
    r = client.get("/events", params={"from_ledger": 101, "to_ledger": 103})
    assert sorted(e["ledger"] for e in r.json()["items"]) == [101, 102, 103, 103]
    r = client.get("/events", params={"from_ledger": 104})
    assert ids_of(r) == [SEED[5]["id"]]
    r = client.get("/events", params={"to_ledger": 100})
    assert ids_of(r) == [SEED[0]["id"]]


def test_combined_filters(client):
    r = client.get(
        "/events", params={"contract_id": TOKEN, "topic0": "transfer", "from_ledger": 101}
    )
    assert ids_of(r) == [SEED[1]["id"]]


def test_invalid_range(client):
    r = client.get("/events", params={"from_ledger": 10, "to_ledger": 5})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "bad_request"


def test_pagination(client):
    page1 = client.get("/events", params={"limit": 2}).json()
    page2 = client.get("/events", params={"limit": 2, "offset": 2}).json()
    page4 = client.get("/events", params={"limit": 2, "offset": 6}).json()
    assert len(page1["items"]) == 2 and len(page2["items"]) == 2
    assert not set(ids_of_json(page1)) & set(ids_of_json(page2))
    assert page4["items"] == [] and page4["total"] == len(SEED)


def ids_of_json(body):
    return [e["id"] for e in body["items"]]


@pytest.mark.parametrize(
    "params", [{"limit": 0}, {"limit": 501}, {"offset": -1}, {"from_ledger": "x"}]
)
def test_limit_bounds(client, params):
    r = client.get("/events", params=params)
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "validation_error"
    assert isinstance(err["details"], list) and err["details"][0]["loc"][0] == "query"


def test_limit_max_ok(client):
    assert client.get("/events", params={"limit": 500}).status_code == 200


def test_contracts(client):
    body = client.get("/contracts").json()
    assert body["items"] == [
        {"contract_id": TOKEN, "event_count": 3, "first_ledger": 100, "last_ledger": 104},
        {"contract_id": OTHER, "event_count": 2, "first_ledger": 102, "last_ledger": 103},
    ]


def test_stats(client):
    body = client.get("/stats").json()
    assert body["total_events"] == 6
    assert body["contracts"] == 2
    assert body["min_ledger"] == 100 and body["max_ledger"] == 104
    assert body["undecodable_events"] == 1
    assert body["events_by_type"] == {"contract": 5, "system": 1}
    assert body["indexer"] == {
        "cursor": "cursor-1",
        "start_ledger": 100,
        "last_seen_ledger": 110,
        "last_poll_at": None,
    }


def test_stats_empty_db(engine):
    with TestClient(create_app(Config(), engine=engine)) as c:
        body = c.get("/stats").json()
    assert body["total_events"] == 0 and body["min_ledger"] is None
    assert body["indexer"]["cursor"] is None


def test_unknown_route_uses_error_envelope(client):
    r = client.get("/nope")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_method_not_allowed(client):
    r = client.post("/events")
    assert r.status_code == 405
    assert r.json()["error"]["code"] == "method_not_allowed"


def test_internal_error_envelope(engine, session_factory):
    app = create_app(Config(), engine=engine)
    with session_factory() as s:
        s.execute(text("DROP TABLE events"))
        s.commit()
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/contracts")
    assert r.status_code == 500
    assert r.json() == {
        "error": {"code": "internal_error", "message": "Internal server error", "details": None}
    }


def test_cors_header(client):
    r = client.get("/health", headers={"Origin": "http://dash.test"})
    assert r.headers["access-control-allow-origin"] == "http://dash.test"


def test_openapi_lists_all_routes(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert set(paths) == {"/health", "/events", "/events/{event_id}", "/contracts", "/stats"}
    assert client.get("/docs").status_code == 200


def test_session_is_not_a_query_parameter(client):
    # Regression guard for the `from __future__ import annotations` pitfall.
    params = client.get("/openapi.json").json()["paths"]["/health"]["get"].get("parameters", [])
    assert params == []


def test_create_app_from_config_url(tmp_path):
    app = create_app(Config(database_url=f"sqlite:///{tmp_path / 'x.db'}"))
    with TestClient(app) as c:
        assert c.get("/health").status_code == 200
