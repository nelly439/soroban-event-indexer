# Configuration

Both processes (`soroban-indexer index` and `soroban-indexer serve`) read their
settings from environment variables. Every variable has a default, so the
service starts with zero configuration: SQLite in the working directory and the
public testnet RPC.

Invalid values stop the process at startup with `configuration error: ...` and
exit code `2`.

When running with Docker Compose, put overrides in a `.env` file next to
`docker-compose.yml` (start from [`.env.example`](../.env.example)).

| Variable | Default | Description |
| --- | --- | --- |
| `DATABASE_URL` | `sqlite:///./soroban_events.db` | SQLAlchemy database URL. SQLite and PostgreSQL (`postgresql+psycopg://user:pass@host:5432/db`) are supported. |
| `RPC_URL` | `https://soroban-testnet.stellar.org` | Stellar RPC endpoint (JSON-RPC over HTTP). |
| `START_LEDGER` | *(unset)* | Ledger to start from on the **first** run. Unset = the latest ledger, i.e. only new events. Must lie inside the RPC node's retention window. Ignored once a cursor is stored (see `reset-cursor`). |
| `CONTRACT_IDS` | *(unset)* | Comma-separated contract addresses (`C...`, 56 characters) to index. Unset = every contract. At most 25 (Stellar RPC allows 5 filters × 5 IDs). Duplicates are removed. |
| `POLL_INTERVAL_SECONDS` | `5` | Pause between polls once the indexer has caught up. Ledgers close roughly every 5–6 s. |
| `PAGE_LIMIT` | `100` | Events per `getEvents` request, `1`–`10000`. Larger pages backfill faster. |
| `RPC_TIMEOUT_SECONDS` | `30` | HTTP timeout per RPC request. Minimum `0.1`. |
| `BACKOFF_INITIAL_SECONDS` | `1` | First retry delay after an RPC error. Doubles on each consecutive failure. |
| `BACKOFF_MAX_SECONDS` | `60` | Upper bound for the retry delay. Must be ≥ `BACKOFF_INITIAL_SECONDS`. |
| `API_HOST` | `0.0.0.0` | Interface the REST API binds to. |
| `API_PORT` | `8000` | Port the REST API listens on. |
| `CORS_ORIGINS` | `*` | Comma-separated origins allowed to call the API from a browser, or `*`. |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL` (case-insensitive). |

## Choosing a start ledger

Stellar RPC nodes keep only a rolling window of history (the node's
`HISTORY_RETENTION_WINDOW`, typically about 7 days on public endpoints). To find
the current window:

```sh
curl -s -X POST "$RPC_URL" -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"getLatestLedger"}'
```

and use a `START_LEDGER` a little above `latest - retention`. If the value is
too old the indexer logs the exact window the node serves and how to fix it.

## Changing the start ledger later

The cursor is stored in the database and always wins over `START_LEDGER`. To
start over from a different ledger:

```sh
soroban-indexer reset-cursor          # keeps indexed events
START_LEDGER=123456 soroban-indexer index
```

## Frontend

The dashboard is a static app. It calls the API at `/api` by default; nginx (in
Docker) and the Vite dev server proxy that path to the API. To call the API on
another origin directly, build with `VITE_API_URL=https://indexer.example.com`
and add that dashboard's origin to `CORS_ORIGINS`.
