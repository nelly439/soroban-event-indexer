"""Builders for realistic getEvents payloads used across the unit tests."""

from __future__ import annotations

from typing import Any

from stellar_sdk import scval
from stellar_sdk import xdr as stellar_xdr

TOKEN = "CDLZFC3SYJYDZT7K67VZ75HPJVIEUVNIXF47ZG2FB2RMQQVU2HHGCYSC"
OTHER = "CAS3J7GYLGXMF6TDJBBYYSE3HQ6BBSMLNUQ34T6TZMYMW2EVH34XOWMA"
ALICE = "GAAZI4TCR3TY5OJHCTJC2A4QSY6CJWJH5IAJTGKIN2ER7LBNVKOCCWN7"
BOB = "GBRPYHIL2CI3FNQ4BXLFMNDLFJUNPU2HY3ZMFSHONUCEOASW7QC7OX2H"


def b64(val: stellar_xdr.SCVal) -> str:
    """Encode an SCVal as base64 XDR."""
    return val.to_xdr()


def event_id(ledger: int, tx: int = 1, op: int = 0, idx: int = 0) -> str:
    """Build an id in the same format as Stellar RPC (TOID-eventIndex)."""
    toid = (ledger << 32) | (tx << 12) | op
    return f"{toid:019d}-{idx:010d}"


def rpc_event(
    ledger: int,
    *,
    tx: int = 1,
    idx: int = 0,
    contract: str | None = TOKEN,
    topics: list[stellar_xdr.SCVal] | None = None,
    value: stellar_xdr.SCVal | None = None,
    type: str = "contract",
    topic_xdr: list[str] | None = None,
    value_xdr: str | None = None,
) -> dict[str, Any]:
    """An element of the getEvents ``events`` array."""
    if topics is None:
        topics = [scval.to_symbol("transfer"), scval.to_address(ALICE), scval.to_address(BOB)]
    if value is None:
        value = scval.to_int128(1000)
    return {
        "type": type,
        "ledger": ledger,
        "ledgerClosedAt": "2026-10-29T12:00:00Z",
        "contractId": contract or "",
        "id": event_id(ledger, tx=tx, idx=idx),
        "operationIndex": 0,
        "transactionIndex": tx,
        "txHash": f"{ledger:064x}",
        "inSuccessfulContractCall": True,
        "topic": topic_xdr if topic_xdr is not None else [b64(t) for t in topics],
        "value": value_xdr if value_xdr is not None else b64(value),
    }
