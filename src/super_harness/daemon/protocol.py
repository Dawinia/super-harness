"""Newline-delimited JSON protocol for the super-harness daemon.

Defines the wire schema documented in daemon-architecture §2.1: a request line
carries `version` / `method` / `params` / `id`; a response line carries
`version` / `id` / `result` / `error`. Version mismatch raises
`ProtocolVersionMismatch` (mapped to HTTP-style 400 by the server, per AC-7).

UC-8 invariant: any line exceeding `MAX_REQUEST_BYTES` is rejected BEFORE
`json.loads()` is attempted — protects daemon from OOM via crafted requests.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "MAX_REQUEST_BYTES",
    "PROTOCOL_VERSION",
    "GateQueryRequest",
    "GateQueryResponse",
    "ProtocolError",
    "ProtocolVersionMismatch",
    "decode_request",
    "decode_response",
    "encode_request",
    "encode_response",
]

PROTOCOL_VERSION: str = "1"
MAX_REQUEST_BYTES: int = 1_048_576  # 1 MiB — UC-8 hard ceiling


class ProtocolError(ValueError):
    """Wire format violation: malformed JSON, missing field, oversized line."""


class ProtocolVersionMismatch(ProtocolError):
    """Request's `version` field does not match `PROTOCOL_VERSION`."""


@dataclass(frozen=True)
class GateQueryRequest:
    """Decoded request line.

    `params` shape depends on `method`; the protocol layer treats it as opaque.
    """

    method: str
    params: dict[str, Any] = field(default_factory=dict)
    id: str | None = None


@dataclass(frozen=True)
class GateQueryResponse:
    """Decoded response line.

    Exactly one of `result` / `error` is non-None in well-formed responses.
    """

    id: str | None
    result: dict[str, Any] | None
    error: dict[str, Any] | None


def encode_request(req: GateQueryRequest) -> bytes:
    payload = {
        "version": PROTOCOL_VERSION,
        "method": req.method,
        "params": req.params,
        "id": req.id,
    }
    return (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")


def encode_response(resp: GateQueryResponse) -> bytes:
    payload = {
        "version": PROTOCOL_VERSION,
        "id": resp.id,
        "result": resp.result,
        "error": resp.error,
    }
    return (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")


def decode_request(line: bytes) -> GateQueryRequest:
    # UC-8: short-circuit before parse attempt
    if len(line) > MAX_REQUEST_BYTES:
        raise ProtocolError(
            f"request exceeds MAX_REQUEST_BYTES ({len(line)} > {MAX_REQUEST_BYTES})"
        )
    try:
        obj = json.loads(line.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ProtocolError(f"malformed request: {exc}") from exc
    if not isinstance(obj, dict):
        raise ProtocolError(f"request must be a JSON object, got {type(obj).__name__}")
    version = obj.get("version")
    if version != PROTOCOL_VERSION:
        raise ProtocolVersionMismatch(
            f"got version={version!r}, want {PROTOCOL_VERSION!r}"
        )
    method = obj.get("method")
    if not isinstance(method, str) or not method:
        raise ProtocolError(f"missing or invalid 'method': {method!r}")
    params = obj.get("params") or {}
    if not isinstance(params, dict):
        raise ProtocolError(f"'params' must be an object, got {type(params).__name__}")
    req_id = obj.get("id")
    if req_id is not None and not isinstance(req_id, str):
        raise ProtocolError(f"'id' must be string or null, got {type(req_id).__name__}")
    return GateQueryRequest(method=method, params=params, id=req_id)


def decode_response(line: bytes) -> GateQueryResponse:
    if len(line) > MAX_REQUEST_BYTES:
        raise ProtocolError(
            f"response exceeds MAX_REQUEST_BYTES ({len(line)} > {MAX_REQUEST_BYTES})"
        )
    try:
        obj = json.loads(line.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ProtocolError(f"malformed response: {exc}") from exc
    if not isinstance(obj, dict):
        raise ProtocolError(f"response must be a JSON object, got {type(obj).__name__}")
    error = obj.get("error")
    # Per daemon-architecture §UC-6: a stale, version-mismatched daemon returns
    # an error envelope stamped with ITS protocol version; the client/supervisor
    # MUST be able to read that error to trigger stop+restart. Error envelopes
    # are therefore decodable regardless of version — only success (result-
    # bearing) responses are version-gated.
    if error is None:
        version = obj.get("version")
        if version != PROTOCOL_VERSION:
            raise ProtocolVersionMismatch(
                f"got version={version!r}, want {PROTOCOL_VERSION!r}"
            )
    return GateQueryResponse(
        id=obj.get("id"),
        result=obj.get("result"),
        error=error,
    )
