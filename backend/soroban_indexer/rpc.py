"""Minimal synchronous JSON-RPC client for the Stellar RPC methods we use.

Only two methods are needed: ``getLatestLedger`` and ``getEvents``. The
client is deliberately thin so that it is easy to test and to swap.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

#: Stellar RPC limits: 5 contract IDs per filter, 5 filters per request.
CONTRACT_IDS_PER_FILTER = 5

_RETENTION_RE = re.compile(
    r"must be between the oldest ledger:\s*(\d+)\s*and the latest ledger:\s*(\d+)", re.I
)


class RpcError(Exception):
    """Any failure talking to the RPC node (network, HTTP status, JSON-RPC error)."""

    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


class RetentionWindowError(RpcError):
    """The requested start ledger / cursor is outside the node's retention window."""

    def __init__(self, message: str, oldest_ledger: int | None, latest_ledger: int | None) -> None:
        super().__init__(message)
        self.oldest_ledger = oldest_ledger
        self.latest_ledger = latest_ledger


@dataclass
class RawEvent:
    """One event as returned by ``getEvents`` (XDR still encoded)."""

    id: str
    type: str
    ledger: int
    ledger_closed_at: str | None
    contract_id: str | None
    tx_hash: str | None
    in_successful_contract_call: bool | None
    topic_xdr: list[str]
    value_xdr: str

    @classmethod
    def from_rpc(cls, data: dict[str, Any]) -> RawEvent:
        """Build from one element of the RPC ``events`` array.

        Accepts both the current shape (``value`` is a base64 string) and the
        older one (``value`` is ``{"xdr": "..."}``).
        """
        value = data.get("value")
        if isinstance(value, dict):
            value = value.get("xdr")
        return cls(
            id=str(data["id"]),
            type=str(data.get("type", "contract")),
            ledger=int(data["ledger"]),
            ledger_closed_at=data.get("ledgerClosedAt"),
            contract_id=data.get("contractId") or None,
            tx_hash=data.get("txHash"),
            in_successful_contract_call=data.get("inSuccessfulContractCall"),
            topic_xdr=list(data.get("topic") or []),
            value_xdr=value if isinstance(value, str) else "",
        )


@dataclass
class EventsPage:
    """A single ``getEvents`` response."""

    events: list[RawEvent]
    cursor: str | None
    latest_ledger: int | None
    oldest_ledger: int | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


def build_filters(contract_ids: tuple[str, ...] | list[str]) -> list[dict[str, Any]]:
    """Build the ``filters`` array, chunking contract IDs 5 per filter.

    With no contract IDs a single empty filter is sent, which matches every event.
    """
    ids = list(contract_ids)
    if not ids:
        return [{}]
    return [
        {"contractIds": ids[i : i + CONTRACT_IDS_PER_FILTER]}
        for i in range(0, len(ids), CONTRACT_IDS_PER_FILTER)
    ]


def build_get_events_params(
    *,
    start_ledger: int | None,
    cursor: str | None,
    limit: int,
    contract_ids: tuple[str, ...] | list[str] = (),
) -> dict[str, Any]:
    """Build ``getEvents`` params. Exactly one of ``start_ledger``/``cursor`` must be set.

    Stellar RPC rejects requests that carry both ``startLedger`` and a
    pagination cursor, so this function enforces the rule up front.
    """
    if (start_ledger is None) == (cursor is None):
        raise ValueError("exactly one of start_ledger or cursor must be provided")
    params: dict[str, Any] = {
        "filters": build_filters(contract_ids),
        "pagination": {"limit": limit},
    }
    if cursor is not None:
        params["pagination"]["cursor"] = cursor
    else:
        params["startLedger"] = start_ledger
    return params


class RpcClient:
    """Synchronous client for a Stellar RPC endpoint."""

    def __init__(
        self,
        url: str,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.url = url
        self._ids = itertools.count(1)
        self._http = httpx.Client(
            timeout=timeout,
            transport=transport,
            headers={"User-Agent": "soroban-event-indexer/0.1"},
        )

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        self._http.close()

    def __enter__(self) -> RpcClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Perform a JSON-RPC call and return ``result``. Raises :class:`RpcError`."""
        body: dict[str, Any] = {"jsonrpc": "2.0", "id": next(self._ids), "method": method}
        if params is not None:
            body["params"] = params
        try:
            resp = self._http.post(self.url, json=body)
        except httpx.HTTPError as exc:
            raise RpcError(f"{method}: transport error: {exc!r}") from exc
        if resp.status_code == 429:
            raise RpcError(f"{method}: rate limited by RPC (HTTP 429)", code=429)
        if resp.status_code >= 400:
            raise RpcError(
                f"{method}: HTTP {resp.status_code}: {resp.text[:200]}", code=resp.status_code
            )
        try:
            payload = resp.json()
        except ValueError as exc:
            raise RpcError(f"{method}: response is not JSON") from exc
        if not isinstance(payload, dict):
            raise RpcError(f"{method}: unexpected response shape")
        if payload.get("error"):
            err = payload["error"]
            message = str(err.get("message", err)) if isinstance(err, dict) else str(err)
            code = err.get("code") if isinstance(err, dict) else None
            match = _RETENTION_RE.search(message)
            if match:
                raise RetentionWindowError(
                    f"{method}: {message}", int(match.group(1)), int(match.group(2))
                )
            raise RpcError(f"{method}: RPC error {code}: {message}", code=code)
        result = payload.get("result")
        if not isinstance(result, dict):
            raise RpcError(f"{method}: response has no result object")
        return result

    def get_latest_ledger(self) -> int:
        """Return the sequence number of the latest ledger known to the node."""
        result = self.call("getLatestLedger")
        try:
            return int(result["sequence"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RpcError("getLatestLedger: missing 'sequence'") from exc

    def get_events(
        self,
        *,
        start_ledger: int | None = None,
        cursor: str | None = None,
        limit: int = 100,
        contract_ids: tuple[str, ...] | list[str] = (),
    ) -> EventsPage:
        """Fetch one page of events starting at ``start_ledger`` or after ``cursor``."""
        params = build_get_events_params(
            start_ledger=start_ledger, cursor=cursor, limit=limit, contract_ids=contract_ids
        )
        result = self.call("getEvents", params)
        try:
            events = [RawEvent.from_rpc(e) for e in result.get("events") or []]
        except (KeyError, TypeError, ValueError) as exc:
            raise RpcError(f"getEvents: malformed event in response: {exc!r}") from exc
        next_cursor = result.get("cursor")
        if not next_cursor and events:
            # Older RPC versions only expose a per-event paging token (== id).
            next_cursor = events[-1].id
        latest = result.get("latestLedger")
        oldest = result.get("oldestLedger")
        return EventsPage(
            events=events,
            cursor=next_cursor or None,
            latest_ledger=int(latest) if latest is not None else None,
            oldest_ledger=int(oldest) if oldest is not None else None,
            raw=result,
        )
