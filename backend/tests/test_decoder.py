import base64

import pytest
from stellar_sdk import scval
from stellar_sdk import xdr as stellar_xdr

from soroban_indexer.decoder import (
    MAX_SAFE_INTEGER,
    UNDECODABLE_KEY,
    DecodeError,
    decode_or_marker,
    decode_scval,
    topic_key,
)

from .helpers import ALICE, TOKEN


def d(val: stellar_xdr.SCVal):
    return decode_scval(val.to_xdr())


@pytest.mark.parametrize(
    ("val", "expected"),
    [
        (scval.to_bool(True), True),
        (scval.to_bool(False), False),
        (scval.to_void(), None),
        (scval.to_symbol("transfer"), "transfer"),
        (scval.to_string("hello wörld"), "hello wörld"),
        (scval.to_uint32(4_294_967_295), 4_294_967_295),
        (scval.to_int32(-2_147_483_648), -2_147_483_648),
        (scval.to_uint64(42), 42),
        (scval.to_int64(-42), -42),
        (scval.to_uint128(7), 7),
        (scval.to_int128(-7), -7),
        (scval.to_uint256(9), 9),
        (scval.to_int256(-9), -9),
        (scval.to_timepoint(1_700_000_000), 1_700_000_000),
        (scval.to_duration(3600), 3600),
        (scval.to_bytes(b"\x00\x01\xab\xff"), "0x0001abff"),
        (scval.to_bytes(b""), "0x"),
        (scval.to_address(ALICE), ALICE),
        (scval.to_address(TOKEN), TOKEN),
    ],
)
def test_scalar_types(val, expected):
    out = d(val)
    assert out == expected
    assert type(out) is type(expected)


def test_vec_and_nested():
    val = scval.to_vec([scval.to_symbol("a"), scval.to_uint32(1), scval.to_vec([scval.to_void()])])
    assert d(val) == ["a", 1, [None]]


def test_empty_vec_and_map():
    assert d(scval.to_vec([])) == []
    assert d(scval.to_map({})) == {}


def test_map_with_symbol_keys():
    val = scval.to_map(
        {
            scval.to_symbol("amount"): scval.to_int128(5),
            scval.to_symbol("to"): scval.to_address(ALICE),
        }
    )
    assert d(val) == {"amount": 5, "to": ALICE}


def test_map_with_non_string_keys_are_json_encoded():
    # Built by hand: scval.to_map() cannot hash vec keys.
    entries = [
        stellar_xdr.SCMapEntry(scval.to_uint32(1), scval.to_bool(True)),
        stellar_xdr.SCMapEntry(
            scval.to_vec([scval.to_symbol("x"), scval.to_uint32(2)]), scval.to_void()
        ),
    ]
    val = stellar_xdr.SCVal(stellar_xdr.SCValType.SCV_MAP, map=stellar_xdr.SCMap(entries))
    assert d(val) == {"1": True, '["x",2]': None}


def test_struct_helper_produces_object():
    val = scval.to_struct({"from": scval.to_address(ALICE), "amount": scval.to_int128(10)})
    assert d(val) == {"amount": 10, "from": ALICE}


@pytest.mark.parametrize(
    ("val", "expected"),
    [
        (scval.to_int64(MAX_SAFE_INTEGER), MAX_SAFE_INTEGER),
        (scval.to_int64(-MAX_SAFE_INTEGER), -MAX_SAFE_INTEGER),
        (scval.to_int64(MAX_SAFE_INTEGER + 1), str(MAX_SAFE_INTEGER + 1)),
        (scval.to_int64(-MAX_SAFE_INTEGER - 1), str(-MAX_SAFE_INTEGER - 1)),
        (scval.to_uint64(2**64 - 1), "18446744073709551615"),
        (scval.to_int128(-(2**127)), str(-(2**127))),
        (scval.to_uint128(2**128 - 1), str(2**128 - 1)),
        (scval.to_uint256(2**256 - 1), str(2**256 - 1)),
        (scval.to_int256(-(2**255)), str(-(2**255))),
    ],
)
def test_large_integers_become_strings(val, expected):
    assert d(val) == expected


def test_large_int_inside_map():
    val = scval.to_map({scval.to_symbol("amount"): scval.to_int128(10**30)})
    assert d(val) == {"amount": "1000000000000000000000000000000"}


def test_invalid_utf8_string_becomes_hex():
    assert d(scval.to_string(b"\xff\xfe")) == "0xfffe"


def test_error_contract_code():
    err = stellar_xdr.SCVal(
        stellar_xdr.SCValType.SCV_ERROR,
        error=stellar_xdr.SCError(
            stellar_xdr.SCErrorType.SCE_CONTRACT, contract_code=stellar_xdr.Uint32(7)
        ),
    )
    assert d(err) == {"$type": "error", "kind": "SCE_CONTRACT", "code": 7}


def test_error_host_code():
    err = stellar_xdr.SCVal(
        stellar_xdr.SCValType.SCV_ERROR,
        error=stellar_xdr.SCError(
            stellar_xdr.SCErrorType.SCE_BUDGET, code=stellar_xdr.SCErrorCode.SCEC_EXCEEDED_LIMIT
        ),
    )
    assert d(err) == {"$type": "error", "kind": "SCE_BUDGET", "code": "SCEC_EXCEEDED_LIMIT"}


def test_exotic_type_is_tagged_with_xdr():
    val = stellar_xdr.SCVal(stellar_xdr.SCValType.SCV_LEDGER_KEY_CONTRACT_INSTANCE)
    out = d(val)
    assert out == {"$type": "ledger_key_contract_instance", "xdr": val.to_xdr()}


@pytest.mark.parametrize(
    "garbage", ["", "not base64!!", "AAAA", "////", base64.b64encode(b"\x00\x00\x00\x63").decode()]
)
def test_garbage_raises_decode_error(garbage):
    with pytest.raises(DecodeError):
        decode_scval(garbage)


def test_non_string_input_raises():
    with pytest.raises(DecodeError):
        decode_scval(None)  # type: ignore[arg-type]


def test_decode_or_marker_never_raises():
    value, ok = decode_or_marker("garbage!!")
    assert ok is False
    assert value[UNDECODABLE_KEY] is True
    assert value["xdr"] == "garbage!!"
    assert "invalid SCVal XDR" in value["error"]


def test_decode_or_marker_success():
    assert decode_or_marker(scval.to_symbol("x").to_xdr()) == ("x", True)


def test_topic_key():
    assert topic_key("transfer") == "transfer"
    assert topic_key(5) == "5"
    assert topic_key(["a", 1]) == '["a",1]'
    assert topic_key({"b": 1, "a": 2}) == '{"a":2,"b":1}'
    assert topic_key(None) == "null"
