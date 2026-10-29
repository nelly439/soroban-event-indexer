"""Decode Soroban ``SCVal`` XDR into plain, JSON-safe Python values.

Leaf values are converted with :func:`stellar_sdk.scval.to_native`; containers
(vec / map) are walked here so that map keys of any type can be turned into
JSON object keys. See ``docs/decoding.md`` for the full mapping table.

Rules in short:

* symbol / string -> ``str``
* bool -> ``bool``; void -> ``None``
* u32 / i32 / u64 / i64 / u128 / i128 / u256 / i256 / timepoint / duration ->
  ``int``, **or a decimal string if the magnitude exceeds 2^53 - 1** so that
  JavaScript clients never lose precision
* address -> strkey (``G...``, ``C...``, ``M...``, ...)
* bytes -> ``"0x"`` + lowercase hex
* vec -> ``list``; map -> ``dict`` (non-string keys are JSON-encoded)
* error -> ``{"$type": "error", ...}``; other exotic types ->
  ``{"$type": <name>, "xdr": <base64>}``

A value that cannot be parsed never raises out of :func:`decode_or_marker`;
it yields an "undecodable" marker that keeps the raw XDR.
"""

from __future__ import annotations

import base64
import json
from typing import Any

from stellar_sdk import Address, scval
from stellar_sdk import xdr as stellar_xdr

#: Largest integer a JavaScript ``number`` represents exactly (2^53 - 1).
MAX_SAFE_INTEGER = 2**53 - 1

#: Key used to mark a value that could not be decoded.
UNDECODABLE_KEY = "$undecodable"

JSONValue = Any


class DecodeError(ValueError):
    """Raised when an XDR string is not a valid ``SCVal``."""


def _safe_int(value: int) -> int | str:
    """Return ``value`` unchanged if JS-safe, otherwise its decimal string."""
    if -MAX_SAFE_INTEGER <= value <= MAX_SAFE_INTEGER:
        return value
    return str(value)


def _key_to_str(key: JSONValue) -> str:
    """Turn a decoded map key into a JSON object key."""
    if isinstance(key, str):
        return key
    return json.dumps(key, separators=(",", ":"), sort_keys=True)


def _decode_error(err: stellar_xdr.SCError) -> dict[str, JSONValue]:
    """Render an ``SCError`` as a small tagged object."""
    out: dict[str, JSONValue] = {"$type": "error", "kind": err.type.name}
    if err.contract_code is not None:
        out["code"] = err.contract_code.uint32
    elif err.code is not None:
        out["code"] = err.code.name
    return out


def scval_to_json(val: stellar_xdr.SCVal) -> JSONValue:
    """Convert a parsed ``SCVal`` into a JSON-safe value (see module docs)."""
    t = val.type
    T = stellar_xdr.SCValType  # noqa: N806
    if t == T.SCV_VEC:
        items = val.vec.sc_vec if val.vec is not None else []
        return [scval_to_json(v) for v in items]
    if t == T.SCV_MAP:
        entries = val.map.sc_map if val.map is not None else []
        return {_key_to_str(scval_to_json(e.key)): scval_to_json(e.val) for e in entries}
    if t == T.SCV_ERROR:
        assert val.error is not None
        return _decode_error(val.error)

    native = scval.to_native(val)
    if isinstance(native, bool) or native is None:
        return native
    if isinstance(native, int):
        return _safe_int(native)
    if isinstance(native, str):
        return native
    if isinstance(native, bytes):
        # SCV_BYTES, or an SCV_STRING whose contents are not valid UTF-8.
        return "0x" + native.hex()
    if isinstance(native, Address):
        return native.address
    # Types to_native leaves untouched (contract instance, ledger keys, ...).
    return {"$type": t.name.removeprefix("SCV_").lower(), "xdr": val.to_xdr()}


def decode_scval(xdr_b64: str) -> JSONValue:
    """Decode a base64 ``SCVal`` string. Raises :class:`DecodeError` if invalid."""
    if not isinstance(xdr_b64, str) or not xdr_b64:
        raise DecodeError("expected a non-empty base64 string")
    try:
        raw = base64.b64decode(xdr_b64, validate=True)
        val = stellar_xdr.SCVal.from_xdr_bytes(raw)
    except Exception as exc:  # noqa: BLE001 - the XDR library raises many types
        raise DecodeError(f"invalid SCVal XDR: {exc}") from exc
    try:
        return scval_to_json(val)
    except Exception as exc:  # noqa: BLE001 - e.g. malformed address payloads
        raise DecodeError(f"could not convert SCVal: {exc}") from exc


def undecodable_marker(xdr_b64: Any, error: str) -> dict[str, JSONValue]:
    """Build the marker stored in place of a value that failed to decode."""
    return {UNDECODABLE_KEY: True, "xdr": xdr_b64, "error": error}


def decode_or_marker(xdr_b64: Any) -> tuple[JSONValue, bool]:
    """Decode ``xdr_b64``; never raises.

    Returns ``(value, ok)``. When decoding fails ``value`` is an
    :func:`undecodable_marker` and ``ok`` is ``False``.
    """
    try:
        return decode_scval(xdr_b64), True
    except DecodeError as exc:
        return undecodable_marker(xdr_b64, str(exc)), False


def topic_key(value: JSONValue) -> str:
    """Canonical string used to index and filter on a decoded topic.

    Strings (symbols, strings, addresses, hex bytes) are used verbatim so that
    ``topic0=transfer`` matches the ``transfer`` symbol. Anything else is
    compact JSON (e.g. ``42`` or ``["a",1]``).
    """
    if isinstance(value, str):
        return value
    return json.dumps(value, separators=(",", ":"), sort_keys=True)
