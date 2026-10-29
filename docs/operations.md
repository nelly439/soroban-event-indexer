# Operating the indexer

## Processes

The service is two processes sharing one database:

* **indexer** (`soroban-indexer index`) polls Stellar RPC `getEvents` and writes
  events. Run exactly **one** per database.
* **api** (`soroban-indexer serve`) answers HTTP requests. It only reads, so you
  can run as many replicas as you like.

## Guarantees

* **No gaps.** The RPC cursor is committed in the same database transaction as
  the events it follows. If the indexer is killed at any point it resumes from
  the last committed page.
* **No duplicates.** The RPC event id is the primary key and inserts ignore
  conflicts, so re-reading a page is harmless.
* **Never crashes on bad data.** Values that fail to decode are stored with a
  marker (see [decoding.md](decoding.md)).
* **Survives RPC outages.** Network errors, HTTP errors and rate limits (HTTP
  429) are retried with exponential backoff up to `BACKOFF_MAX_SECONDS`.
* **Graceful shutdown.** `SIGINT`/`SIGTERM` stop the loop after the current
  page; Docker's default 10 s stop timeout is plenty.

## The retention window

RPC nodes keep only recent history. If the indexer is stopped for longer than
the node's window, the stored cursor points at pruned ledgers and the indexer
logs an error like:

```
RPC retention window error: The stored cursor points at ledgers the RPC node has
already pruned ...; the node currently serves ledgers 51230000..51350000.
How to fix: Run `soroban-indexer reset-cursor` and set START_LEDGER ...
```

The indexer keeps retrying (so it recovers if you swap `RPC_URL` to a node with
longer retention), but it will not skip ahead on its own: losing events must be
your decision. Follow the printed instructions.

## CLI

```text
soroban-indexer index [--once]   run the indexer (--once: one poll, print JSON summary)
soroban-indexer serve            run the REST API
soroban-indexer init-db          create tables (both commands do this automatically)
soroban-indexer reset-cursor     forget the cursor/start ledger; keeps events
soroban-indexer status           print counters and indexer state as JSON
```

Exit codes: `0` success, `1` RPC failure during `index --once`, `2` invalid configuration.

## SQLite or PostgreSQL

SQLite (the default) is fine for a few contracts and a single host; WAL mode is
enabled so the API can read while the indexer writes. Use PostgreSQL when you
index all contracts on mainnet, need several API hosts, or want managed
backups. `docker compose -f docker-compose.yml -f docker-compose.postgres.yml up -d`
runs the stack on PostgreSQL.

Switching databases does not migrate data: point `DATABASE_URL` at the new
database and the indexer starts again from `START_LEDGER`.

## Backups

* SQLite: `sqlite3 soroban_events.db ".backup backup.db"` (safe while running).
* PostgreSQL: `pg_dump`.

Because the cursor lives in the same database as the events, restoring a backup
also restores the matching cursor and the indexer resumes from there (provided
that ledger is still inside the RPC retention window).

## Monitoring

* `GET /health` — API and database reachable.
* `GET /stats` → `indexer.last_poll_at` — should advance every
  `POLL_INTERVAL_SECONDS`; alert if it is older than a few minutes.
* `indexer.last_seen_ledger - max_ledger` is not a reliable lag measure when
  you filter by contract (quiet contracts emit nothing for long periods); use
  `last_poll_at` instead.
