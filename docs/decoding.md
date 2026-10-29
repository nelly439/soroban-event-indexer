# How event data is decoded

Soroban events carry their topics and value as base64-encoded `SCVal` XDR. The
indexer decodes each one to plain JSON when it stores the event, and also keeps
the original XDR (`topics_xdr`, `value_xdr`), so nothing is lost if you prefer to
decode it yourself.

## Mapping

| SCVal type | JSON | Example |
| --- | --- | --- |
| `bool` | `true` / `false` | `true` |
| `void` | `null` | `null` |
| `u32`, `i32` | number | `42` |
| `u64`, `i64`, `u128`, `i128`, `u256`, `i256`, `timepoint`, `duration` | number, **or a decimal string if \|n\| > 2^53 − 1** | `1000`, `"1180591620717411303424"` |
| `symbol`, `string` | string | `"transfer"` |
| `string` that is not valid UTF-8 | `"0x"` + hex | `"0xff00"` |
| `bytes` | `"0x"` + lowercase hex | `"0xdeadbeef"` |
| `address` | strkey | `"GAAZ…"`, `"CDLZ…"`, `"M…"` |
| `vec` | array (recursively decoded) | `["a", 1]` |
| `map` | object (recursively decoded) | `{"amount": 5}` |
| `error` | `{"$type": "error", "kind": ..., "code": ...}` | `{"$type": "error", "kind": "SCE_CONTRACT", "code": 7}` |
| anything else (contract instance, ledger keys, …) | `{"$type": <name>, "xdr": <base64>}` | `{"$type": "contract_instance", "xdr": "…"}` |

### Large integers

JavaScript numbers lose precision above 2^53 − 1 (`9007199254740991`). Token
amounts (`i128`) often exceed that, so any integer outside
`[-(2^53 − 1), 2^53 − 1]` is returned as a **decimal string**. Smaller integers
are plain JSON numbers. Clients that need exact arithmetic should parse both
forms, e.g. `BigInt(value)` in JavaScript or `int(value)` in Python.

### Map keys

JSON object keys must be strings. Keys that decode to a string (symbols,
strings, addresses, bytes) are used as-is. Any other key is replaced by its
compact JSON encoding with sorted keys, e.g. the `u32` key `1` becomes `"1"`
and the `vec` key `["a", 1]` becomes `"[\"a\",1]"`. If two keys produce the
same string the last one wins.

## Undecodable values

If a topic or the value is not valid XDR the event is **still stored**. The
failing part is replaced by a marker and `decode_ok` is set to `false`:

```json
{"$undecodable": true, "xdr": "<the original string>", "error": "invalid SCVal XDR: ..."}
```

`GET /stats` reports the number of such events as `undecodable_events`, and the
dashboard highlights them.

## The `topic0` filter

The first topic of an event is usually its name (`transfer`, `mint`, `swap`, …).
It is stored in an indexed column so `GET /events?topic0=...` is fast. Matching
is exact on the decoded value:

* strings, symbols, addresses and bytes match verbatim: `topic0=transfer`,
  `topic0=GAAZ…`, `topic0=0xdeadbeef`;
* anything else matches its compact JSON: `topic0=42`, `topic0=true`,
  `topic0=["a",1]` (URL-encode it).

Events with no topics, or whose first topic could not be decoded, never match
a `topic0` filter.
