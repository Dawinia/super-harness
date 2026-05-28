"""Unit tests for daemon.client per daemon-architecture §3.5 Layer 1.

The client is the pure protocol client: opens UDS, sends one line, reads one
line, decodes via protocol.decode_response, returns the result dict or raises
DaemonUnreachable / DaemonTimeout. Protocol-level errors (`error` envelope
from server) are returned as part of the response dict — the supervisor (Layer
2) decides what to do; the client is policy-free.
"""
from __future__ import annotations

import hashlib
import os
import socket
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from super_harness.daemon._uds_path import UDS_PATH_MAX
from super_harness.daemon.client import (
    DaemonTimeout,
    DaemonUnreachable,
    query,
)
from super_harness.daemon.protocol import (
    GateQueryResponse,
    decode_request,
    encode_response,
)


def _short_sock_path(tmp_path: Path, name: str) -> Path:
    """Return a UDS-safe socket path under tmp_path, falling back to $TMPDIR.

    macOS' AF_UNIX sun_path is 104 bytes. pytest's `tmp_path` lives under
    `/private/var/folders/.../pytest-of-<user>/pytest-N/<test>/` which is
    ~120+ bytes before the filename — too long to bind directly. We mirror
    `_uds_path.resolve_socket_path`'s fallback: $TMPDIR/super-harness-<hash>.sock,
    keyed on the resolved tmp_path so cleanup remains test-scoped.
    """
    default = tmp_path / name
    if len(str(default).encode("utf-8")) <= UDS_PATH_MAX:
        return default
    digest = hashlib.sha256(str(tmp_path.resolve()).encode("utf-8")).hexdigest()[:16]
    fallback = Path(os.environ.get("TMPDIR", "/tmp")) / f"super-harness-test-{digest}-{name}"
    # Guard against residue from a crashed prior test run colliding on the
    # same digest — bind() fails if the file already exists.
    fallback.unlink(missing_ok=True)
    return fallback


class _EchoServer:
    """Minimal one-shot UDS echo for client unit tests.

    Spawns one thread that accepts a single connection, reads one line,
    invokes `handler(request)` → response bytes, writes them back, closes.
    No HotState, no method registry — only the wire shape under test.
    """

    def __init__(
        self,
        socket_path: Path,
        handler: Any,
        *,
        delay_before_reply: float = 0.0,
    ) -> None:
        self.socket_path = socket_path
        self._handler = handler
        self._delay = delay_before_reply
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind(str(socket_path))
        self._sock.listen(1)
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _serve(self) -> None:
        try:
            conn, _ = self._sock.accept()
        except OSError:
            return
        try:
            line = conn.makefile("rb").readline()
            req = decode_request(line)
            if self._delay:
                time.sleep(self._delay)
            resp_bytes = self._handler(req)
            try:
                conn.sendall(resp_bytes)
            except BrokenPipeError:
                pass  # client timed out and closed first — expected in slow-daemon test
        finally:
            conn.close()
            self._sock.close()


def _ok_handler(req: Any) -> bytes:
    return encode_response(
        GateQueryResponse(
            id=req.id,
            result={"decision": "allow", "reason": "PLAN_APPROVED",
                    "current_state": "PLAN_APPROVED"},
            error=None,
        )
    )


def _proto_error_handler(req: Any) -> bytes:
    return encode_response(
        GateQueryResponse(
            id=req.id,
            result=None,
            error={"code": 400, "message": "ProtocolVersionMismatch"},
        )
    )


def test_query_round_trip_with_live_server(tmp_path: Path) -> None:
    sock_path = _short_sock_path(tmp_path, "daemon.sock")
    server = _EchoServer(sock_path, _ok_handler)
    server.start()
    resp = query(
        sock_path,
        method="gate.pre_tool_use",
        params={"tool": "Edit", "file": "src/foo.py", "change_id": "c1"},
    )
    assert resp["error"] is None
    assert resp["result"]["decision"] == "allow"
    assert resp["result"]["current_state"] == "PLAN_APPROVED"


def test_query_raises_DaemonUnreachable_when_socket_missing(tmp_path: Path) -> None:
    missing = tmp_path / "no-such.sock"
    with pytest.raises(DaemonUnreachable):
        query(missing, method="ping", params={})


def test_query_raises_DaemonUnreachable_on_connect_refused(tmp_path: Path) -> None:
    """Socket file exists (stale from crashed daemon) but nobody is listening."""
    stale = _short_sock_path(tmp_path, "stale.sock")
    # Bind + close without listen handoff — file remains but no accept loop.
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(str(stale))
    s.close()
    with pytest.raises(DaemonUnreachable):
        query(stale, method="ping", params={}, timeout=0.5)


def test_query_raises_DaemonTimeout_on_slow_daemon(tmp_path: Path) -> None:
    sock_path = _short_sock_path(tmp_path, "slow.sock")
    server = _EchoServer(sock_path, _ok_handler, delay_before_reply=1.0)
    server.start()
    with pytest.raises(DaemonTimeout):
        query(sock_path, method="ping", params={}, timeout=0.2)


def test_query_propagates_ProtocolError_in_response(tmp_path: Path) -> None:
    """UC-6: server returns 400 error envelope → client returns the envelope
    (does NOT raise — protocol-level errors are policy decisions, not transport
    failures). The supervisor (Layer 2) decides to restart, fallback, etc.
    """
    sock_path = _short_sock_path(tmp_path, "proto-err.sock")
    server = _EchoServer(sock_path, _proto_error_handler)
    server.start()
    resp = query(sock_path, method="gate.pre_tool_use", params={})
    assert resp["result"] is None
    assert resp["error"] == {"code": 400, "message": "ProtocolVersionMismatch"}
