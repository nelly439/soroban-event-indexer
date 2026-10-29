# soroban-event-indexer

A self-hostable indexer for [Soroban](https://developers.stellar.org/docs/build/smart-contracts)
smart-contract events. It polls a Stellar RPC node, decodes every event's XDR
into plain JSON, stores it in SQLite or PostgreSQL, and serves it through a REST
API and a small web dashboard.

```
Stellar RPC ──getEvents──▶ indexer ──▶ SQLite / PostgreSQL ◀── API (FastAPI) ◀── dashboard (React)
```

* **Correct by construction**: the RPC cursor is committed in the same
  transaction as the events, and event ids are primary keys, so crashes and
  restarts never cause gaps or duplicates.
* **Readable data**: topics and values are decoded (`transfer`, addresses as
  `G…`/`C…`, bytes as hex). Integers beyond 2^53 come back as strings so
  JavaScript clients never lose precision. The raw XDR is kept too.
* **Resilient**: RPC errors and rate limits back off exponentially, undecodable
  values are stored with a marker instead of crashing, and retention-window
  problems produce instructions you can act on.
* **Zero-config start**: defaults to SQLite and the public testnet RPC.

## Quick start (Docker)

```sh
cp .env.example .env          # optional: set CONTRACT_IDS, START_LEDGER, RPC_URL, ...
docker compose up -d --build
```

* Dashboard: <http://localhost:8080>
* API: <http://localhost:8000> (interactive docs at `/docs`)

With no `START_LEDGER` the indexer starts at the current ledger, so on testnet
events show up within seconds.

Variants:

```sh
# PostgreSQL instead of SQLite
docker compose -f docker-compose.yml -f docker-compose.postgres.yml up -d --build

# Offline demo: a fake RPC that produces synthetic events every 5 s (no network needed)
docker compose -f docker-compose.yml -f docker-compose.demo.yml up -d --build
```

`make help` lists shortcuts for these (`make up`, `make up-postgres`, `make up-demo`, `make down`).

## Quick start (without Docker)

Requires Python ≥ 3.11 (and Node ≥ 20 for the dashboard).

```sh
make install                          # backend venv + frontend deps

export CONTRACT_IDS=CDLZFC3SYJYDZT7K67VZ75HPJVIEUVNIXF47ZG2FB2RMQQVU2HHGCYSC   # optional
backend/.venv/bin/soroban-indexer index    # terminal 1: the indexer
backend/.venv/bin/soroban-indexer serve    # terminal 2: the API on :8000
cd frontend && npm run dev                  # terminal 3: dashboard on :5173
```

Or run everything against the fake RPC with one command: `make demo`.

## Using the API

```sh
curl 'localhost:8000/events?contract_id=CDLZ...&topic0=transfer&limit=10'
curl 'localhost:8000/events/0004299262267392000-0000000000'
curl  localhost:8000/contracts
curl  localhost:8000/stats
```

Filters: `contract_id`, `type`, `topic0`, `from_ledger`, `to_ledger`, `limit`
(≤ 500), `offset`. Errors always look like
`{"error": {"code": "...", "message": "..."}}`. See [docs/api.md](docs/api.md).

## Documentation

| Document | Contents |
| --- | --- |
| [docs/configuration.md](docs/configuration.md) | Every environment variable, choosing a start ledger. |
| [docs/api.md](docs/api.md) | Endpoints, parameters, response and error shapes. |
| [docs/decoding.md](docs/decoding.md) | How `SCVal` XDR maps to JSON; big integers; `topic0` matching. |
| [docs/operations.md](docs/operations.md) | Guarantees, retention window, CLI, backups, monitoring. |

## Repository layout

```
backend/      Python package soroban_indexer (indexer, decoder, API, CLI) + unit tests
frontend/     React + TypeScript dashboard (Vite), served by nginx in Docker
tests-e2e/    Fake Stellar RPC server and end-to-end tests that run the real CLI
scripts/      docs_check.py (keeps docs in sync with code), demo.sh
docs/         User documentation
```

## Development

```sh
make check        # lint, mypy --strict, tsc, docs check, unit tests, e2e tests
make test         # unit tests only (backend + frontend)
make e2e          # end-to-end tests
```

Backend unit tests run on SQLite by default. Set
`TEST_DATABASE_URL=postgresql+psycopg://user:pass@host:5432/db` to run them
against PostgreSQL (the test database is wiped).

## Limitations

* Stellar RPC only serves a rolling window of recent history (about 7 days on
  public nodes). To backfill older events you need an RPC node configured with
  a longer retention window.
* `CONTRACT_IDS` accepts at most 25 contracts (an RPC limit: 5 filters × 5 IDs).
  To index more, leave it empty (all contracts) or run several indexers with
  separate databases.
* Only one indexer process may write to a given database.

## License

[MIT](LICENSE)
