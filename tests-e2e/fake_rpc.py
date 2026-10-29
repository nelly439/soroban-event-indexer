"""A tiny fake Stellar RPC server for end-to-end tests and the local demo.

Implements ``getLatestLedger``, ``getEvents`` and ``getHealth`` with the same
cursor semantics as Stellar RPC: the cursor is the id of the last returned
event, and a short page moves the cursor to the end of the scanned range.

Usage::

    python tests-e2e/fake_rpc.py --port 8900 [--ledgers 50] [--tick 2]

``--tick N`` closes a new ledger (with a few new events) every N seconds,
which makes the dashboard look alive during a demo. ``--oldest`` simulates a
retention window. Only the standard library and stellar-sdk are needed.
"""

from __future__ import annotations

import argparse
import json
import random
import threading
import time
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from stellar_sdk import scval

TOKEN = "CDLZFC3SYJYDZT7K67VZ75HPJVIEUVNIXF47ZG2FB2RMQQVU2HHGCYSC"
DEX = "CAS3J7GYLGXMF6TDJBBYYSE3HQ6BBSMLNUQ34T6TZMYMW2EVH34XOWMA"
ACCOUNTS = [
    "GAAZI4TCR3TY5OJHCTJC2A4QSY6CJWJH5IAJTGKIN2ER7LBNVKOCCWN7",
    "GBRPYHIL2CI3FNQ4BXLFMNDLFJUNPU2HY3ZMFSHONUCEOASW7QC7OX2H",
    "GCEZWKCA5VLDNRLN3RPRJMRZOX3Z6G5CHCGSNFHEYVXM3XOJMDS674JZ",
]
FIRST_LEDGER = 1000
GENESIS = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


def event_id(ledger: int, tx: int, idx: int) -> str:
    """Same id format as Stellar RPC: zero-padded TOID, dash, event index."""
    return f"{(ledger << 32) | (tx << 12):019d}-{idx:010d}"


def make_ledger_events(ledger: int, rng: random.Random) -> list[dict[str, Any]]:
    """Generate a deterministic mix of transfer / mint / swap events for one ledger."""
    events = []
    for tx in range(1, rng.randint(1, 3) + 1):
        kind = rng.choice(["transfer", "transfer", "mint", "swap"])
        a, b = rng.sample(ACCOUNTS, 2)
        if kind == "transfer":
            contract = TOKEN
            topics = [scval.to_symbol("transfer"), scval.to_address(a), scval.to_address(b)]
            value = scval.to_int128(rng.randint(1, 10**9) * 10**7)
        elif kind == "mint":
            contract = TOKEN
            topics = [scval.to_symbol("mint"), scval.to_address(a)]
            value = scval.to_int128(2**70 + ledger)  # exceeds 2^53: returned as a string
        else:
            contract = DEX
            topics = [scval.to_symbol("swap"), scval.to_address(a)]
            value = scval.to_map(
                {
                    scval.to_symbol("amount_in"): scval.to_uint64(rng.randint(1, 10**6)),
                    scval.to_symbol("amount_out"): scval.to_uint64(rng.randint(1, 10**6)),
                    scval.to_symbol("pool"): scval.to_bytes(bytes(rng.randbytes(8))),
                }
            )
        events.append(
            {
                "type": "contract",
                "ledger": ledger,
                "ledgerClosedAt": (GENESIS + timedelta(seconds=5 * (ledger - FIRST_LEDGER)))
                .isoformat()
                .replace("+00:00", "Z"),
                "contractId": contract,
                "id": event_id(ledger, tx, 0),
                "operationIndex": 0,
                "transactionIndex": tx,
                "txHash": f"{ledger:032x}{tx:032x}",
                "inSuccessfulContractCall": True,
                "topic": [t.to_xdr() for t in topics],
                "value": value.to_xdr(),
            }
        )
    return events


class Chain:
    """In-memory ledger history shared by request handlers."""

    def __init__(self, ledgers: int, seed: int, oldest: int | None) -> None:
        self.rng = random.Random(seed)
        self.lock = threading.Lock()
        self.events: list[dict[str, Any]] = []
        self.latest = FIRST_LEDGER - 1
        self.oldest = oldest if oldest is not None else FIRST_LEDGER
        self.fail_next = 0
        for _ in range(ledgers):
            self.close_ledger()

    def close_ledger(self) -> None:
        with self.lock:
            self.latest += 1
            self.events.extend(make_ledger_events(self.latest, self.rng))

    def get_events(self, params: dict[str, Any]) -> dict[str, Any]:
        pagination = params.get("pagination") or {}
        limit = int(pagination.get("limit", 100))
        cursor = pagination.get("cursor")
        start = params.get("startLedger")
        if (cursor is None) == (start is None):
            raise RpcFaultError(-32602, "startLedger and cursor cannot both be set (or both unset)")
        ids: set[str] = set()
        for f in params.get("filters") or [{}]:
            ids.update(f.get("contractIds") or [])
        with self.lock:
            if start is not None and not (self.oldest <= int(start) <= self.latest):
                raise RpcFaultError(
                    -32600,
                    f"startLedger must be between the oldest ledger: {self.oldest} "
                    f"and the latest ledger: {self.latest}",
                )
            matching = [e for e in self.events if not ids or e["contractId"] in ids]
            if cursor is not None:
                matching = [e for e in matching if e["id"] > cursor]
            else:
                matching = [e for e in matching if e["ledger"] >= int(start)]
            page = matching[:limit]
            full = len(page) == limit
            next_cursor = page[-1]["id"] if full else event_id(self.latest + 1, 0, 0)
            return {
                "events": page,
                "cursor": next_cursor,
                "latestLedger": self.latest,
                "oldestLedger": self.oldest,
            }


class RpcFaultError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def make_handler(chain: Chain) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: Any) -> None:  # keep test output quiet
            pass

        def _send(self, status: int, body: dict[str, Any]) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self) -> None:  # noqa: N802 - http.server API
            length = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(length) or b"{}")
            rid = req.get("id")
            if chain.fail_next > 0:
                chain.fail_next -= 1
                self._send(503, {"error": "temporarily unavailable"})
                return
            try:
                method = req.get("method")
                if method == "getLatestLedger":
                    result: dict[str, Any] = {"sequence": chain.latest, "protocolVersion": 23}
                elif method == "getHealth":
                    result = {"status": "healthy", "latestLedger": chain.latest}
                elif method == "getEvents":
                    result = chain.get_events(req.get("params") or {})
                elif method == "_closeLedger":  # test hook
                    chain.close_ledger()
                    result = {"sequence": chain.latest}
                elif method == "_failNext":  # test hook: next N requests return HTTP 503
                    chain.fail_next = int((req.get("params") or {}).get("count", 1))
                    result = {}
                else:
                    raise RpcFaultError(-32601, f"method not found: {method}")
            except RpcFaultError as fault:
                error = {"code": fault.code, "message": fault.message}
                self._send(200, {"jsonrpc": "2.0", "id": rid, "error": error})
                return
            self._send(200, {"jsonrpc": "2.0", "id": rid, "result": result})

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8900)
    parser.add_argument("--ledgers", type=int, default=50, help="ledgers to pre-generate")
    parser.add_argument("--tick", type=float, default=0, help="close a ledger every N seconds")
    parser.add_argument("--oldest", type=int, default=None, help="oldest retained ledger")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    chain = Chain(args.ledgers, args.seed, args.oldest)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(chain))
    if args.tick > 0:

        def ticker() -> None:
            while True:
                time.sleep(args.tick)
                chain.close_ledger()

        threading.Thread(target=ticker, daemon=True).start()
    url = f"http://{args.host}:{args.port}"
    print(f"fake RPC on {url} (ledgers {FIRST_LEDGER}..{chain.latest})", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
