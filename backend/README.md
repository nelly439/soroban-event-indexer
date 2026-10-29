# soroban-event-indexer backend

Python package `soroban_indexer`: the polling indexer, the decoder and the REST
API. See the [project README](../README.md) for an overview and the
[docs](../docs) for configuration, decoding rules and the API reference.

## Development

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pip install --no-deps -e .

.venv/bin/pytest --cov            # unit tests + coverage
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/mypy soroban_indexer    # strict
```

Layout:

| Module | Responsibility |
| --- | --- |
| `config.py` | Environment variables → validated `Config` (single source of truth: `ENV_VARS`). |
| `rpc.py` | Thin JSON-RPC client for `getLatestLedger` / `getEvents`; error classification. |
| `decoder.py` | `SCVal` XDR → JSON-safe values. |
| `db.py` | SQLAlchemy models and queries (SQLite / PostgreSQL). |
| `indexer.py` | Polling loop: pagination, atomic cursor, backoff. |
| `api.py` | FastAPI app. |
| `cli.py` | `soroban-indexer` command. |
