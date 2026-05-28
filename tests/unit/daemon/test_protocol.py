"""Unit tests for daemon protocol encode/decode per daemon-architecture §2.1."""
from __future__ import annotations

import json

import pytest

from super_harness.daemon.protocol import (
    MAX_REQUEST_BYTES,
    PROTOCOL_VERSION,
    GateQueryRequest,
    GateQueryResponse,
    ProtocolError,
    ProtocolVersionMismatch,
    decode_request,
    decode_response,
    encode_request,
    encode_response,
)


def test_gate_query_round_trip() -> None:
    req = GateQueryRequest(
        method="gate.pre_tool_use",
        params={"tool": "Edit", "file": "src/foo.py", "change_id": "c1"},
        id="01H8XYZ",
    )
    line = encode_request(req)
    assert line.endswith(b"\n")
    assert b"\n" not in line[:-1]  # newline-delimited contract
    back = decode_request(line)
    assert back == req


def test_version_mismatch_rejected() -> None:
    bad = json.dumps({"version": "9.9.9", "method": "ping", "params": {}, "id": None})
    with pytest.raises(ProtocolVersionMismatch):
        decode_request(bad.encode() + b"\n")


def test_oversized_request_rejected() -> None:
    """UC-8: line > MAX_REQUEST_BYTES rejected WITHOUT attempting JSON parse."""
    # 2MB of pure garbage — must not even attempt json.loads
    huge = b"x" * (MAX_REQUEST_BYTES + 1)
    with pytest.raises(ProtocolError) as exc:
        decode_request(huge)
    # Must not mention JSON — proves we short-circuited before parse
    assert "json" not in str(exc.value).lower()


def test_malformed_json_rejected() -> None:
    truncated = b'{"version": "1", "method": "ping"'  # no closing brace
    with pytest.raises(ProtocolError):
        decode_request(truncated + b"\n")


def test_response_with_error() -> None:
    resp = GateQueryResponse(
        id="01H8XYZ",
        result=None,
        error={"code": 400, "message": "bad request"},
    )
    line = encode_response(resp)
    back = decode_response(line)
    assert back == resp


def test_response_success_round_trip() -> None:
    resp = GateQueryResponse(
        id="01H8XYZ",
        result={"decision": "allow", "reason": "PLAN_APPROVED"},
        error=None,
    )
    assert decode_response(encode_response(resp)) == resp


def test_frozen_dataclasses() -> None:
    from dataclasses import FrozenInstanceError

    req = GateQueryRequest(method="ping", params={}, id=None)
    with pytest.raises(FrozenInstanceError):
        req.method = "gate.pre_tool_use"  # type: ignore[misc]
    resp = GateQueryResponse(id=None, result={}, error=None)
    with pytest.raises(FrozenInstanceError):
        resp.id = "x"  # type: ignore[misc]


def test_protocol_version_constant() -> None:
    assert PROTOCOL_VERSION == "1"
    assert MAX_REQUEST_BYTES == 1_048_576


def test_decode_response_allows_cross_version_error_envelope() -> None:
    """UC-6: a stale daemon's error envelope is stamped with ITS version;
    the client must still decode it (to read the 400 and trigger respawn).
    Error envelopes are NOT version-gated."""
    raw = json.dumps({
        "version": "9.9.9",  # stale daemon's version, != our PROTOCOL_VERSION
        "id": None,
        "result": None,
        "error": {"code": 400, "message": "ProtocolVersionMismatch: got '1', want '9.9.9'"},
    }).encode() + b"\n"
    resp = decode_response(raw)  # must NOT raise
    assert resp.error == {"code": 400,
                          "message": "ProtocolVersionMismatch: got '1', want '9.9.9'"}
    assert resp.result is None


def test_decode_response_still_gates_success_on_version() -> None:
    """Success (result-bearing) responses ARE still version-gated: a daemon
    returning a result on a different protocol version can't be trusted."""
    raw = json.dumps({
        "version": "9.9.9",
        "id": "x",
        "result": {"decision": "allow", "reason": "PLAN_APPROVED"},
        "error": None,
    }).encode() + b"\n"
    with pytest.raises(ProtocolVersionMismatch):
        decode_response(raw)
