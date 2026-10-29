# REST API

The API is read-only and served by `soroban-indexer serve` (port `8000` by
default). Interactive documentation generated from the code is available at
`/docs` (Swagger UI) and `/redoc`; the raw schema is at `/openapi.json`.

All responses are JSON. Large integers inside decoded values are strings — see
[decoding.md](decoding.md).

## Errors

Every non-2xx response uses the same envelope:

```json
{"error": {"code": "not_found", "message": "event 'x' not found", "details": null}}
```

| Status | `code` | When |
| --- | --- | --- |
| 400 | `bad_request` | Logically invalid parameters (e.g. `from_ledger > to_ledger`). |
| 404 | `not_found` | Unknown event id or path. |
| 405 | `method_not_allowed` | Anything other than `GET`. |
| 422 | `validation_error` | Parameter has the wrong type or is out of range; `details` lists each problem. |
| 500 | `internal_error` | Unexpected server error (details are logged, not returned). |
| 503 | `unavailable` | `/health` could not reach the database. |

## Endpoints

### `GET /health`

`200 {"status": "ok", "database": "ok", "version": "0.1.0"}` when the API and
the database are up; `503` otherwise. Suitable for load balancer and container
health checks.

### `GET /events`

Events, newest first (ledger descending, then id descending).

| Parameter | Type | Description |
| --- | --- | --- |
| `contract_id` | string | Exact contract address (`C...`). |
| `type` | `contract` \| `system` \| `diagnostic` | Event type. |
| `topic0` | string | Exact match on the first decoded topic (see [decoding.md](decoding.md#the-topic0-filter)). |
| `from_ledger` | integer ≥ 0 | Inclusive lower bound. |
| `to_ledger` | integer ≥ 0 | Inclusive upper bound. |
| `limit` | 1–500, default 50 | Page size. |
| `offset` | integer ≥ 0, default 0 | Rows to skip. |

```sh
curl 'http://localhost:8000/events?topic0=transfer&limit=2'
```

```json
{
  "items": [
    {
      "id": "0004299262267392000-0000000000",
      "ledger": 1001,
      "ledger_closed_at": "2026-10-29T12:00:05Z",
      "contract_id": "CDLZFC3SYJYDZT7K67VZ75HPJVIEUVNIXF47ZG2FB2RMQQVU2HHGCYSC",
      "type": "contract",
      "tx_hash": "00000000000000000000000000003e9…",
      "in_successful_contract_call": true,
      "topics": ["transfer", "GAAZI4TC…CWN7", "GBRPYHIL…OX2H"],
      "value": "97678730200000000",
      "topics_xdr": ["AAAADwAAAAh0cmFuc2Zlcg==", "…", "…"],
      "value_xdr": "AAAACgAAAAAAAAAAAVrLm1i+hAA=",
      "decode_ok": true
    }
  ],
  "total": 37,
  "limit": 2,
  "offset": 0
}
```

`total` is the number of events matching the filters, so clients can page with
`offset += limit` until `offset >= total`.

### `GET /events/{id}`

One event by its RPC id (same shape as an item above), or `404`.

### `GET /contracts`

Every contract that has at least one indexed event, busiest first:

```json
{"items": [{"contract_id": "CDLZ…", "event_count": 120, "first_ledger": 1000, "last_ledger": 1049}]}
```

### `GET /stats`

Database counters plus the indexer's progress, written by the indexer on every
poll:

```json
{
  "total_events": 120,
  "contracts": 2,
  "min_ledger": 1000,
  "max_ledger": 1049,
  "undecodable_events": 0,
  "events_by_type": {"contract": 120},
  "indexer": {
    "cursor": "0004514010834280448-0000000000",
    "start_ledger": 1000,
    "last_seen_ledger": 1049,
    "last_poll_at": "2026-10-29T16:20:01.123456+00:00"
  }
}
```

If `last_poll_at` stops advancing, the indexer process is down or stuck; check
its logs.
