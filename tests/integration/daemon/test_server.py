"""Integration tests for DaemonServer per daemon-architecture §3.2 + §3.4.

These tests exercise a real AF_UNIX socket on a tmp_path workspace.
Each test spawns the server on a background thread, drives one or more
client connections, then triggers `shutdown()`.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import stat
import threading
import time
from pathlib import Path
from typing import Any

import pytest
import yaml

from super_harness.daemon.protocol import (
    PROTOCOL_VERSION,
    GateQueryRequest,
    decode_response,
    encode_request,
)
from super_harness.daemon.server import DaemonServer


def _write_state(state_path: Path, change_id: str, current_state: str) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        yaml.safe_dump(
            {"changes": {change_id: {"change_id": change_id, "current_state": current_state}}}
        )
    )


def _start_server(server: DaemonServer) -> threading.Thread:
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    # Poll connect() success (not file existence): the socket file is created
    # at bind() but accepts connections only after listen(). On a loaded
    # system, racing into the bind→listen window yields ConnectionRefusedError.
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                probe.settimeout(0.05)
                probe.connect(str(server.socket_path))
            return t
        except (FileNotFoundError, ConnectionRefusedError, OSError):
            time.sleep(0.01)
    raise RuntimeError(f"daemon socket never accepted at {server.socket_path}")


def _request(socket_path: Path, method: str, params: dict[str, Any]) -> dict[str, Any]:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(2.0)
    try:
        s.connect(str(socket_path))
        s.sendall(encode_request(GateQueryRequest(method=method, params=params, id="t1")))
        line = s.makefile("rb").readline()
    finally:
        s.close()
    resp = decode_response(line)
    return {"result": resp.result, "error": resp.error, "id": resp.id}


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / ".harness").mkdir()
    return tmp_path


def _make_server(workspace: Path) -> DaemonServer:
    return DaemonServer(
        socket_path=workspace / ".harness" / "daemon.sock",
        state_path=workspace / ".harness" / "state.yaml",
        events_path=workspace / ".harness" / "events.jsonl",
    )


def test_gate_query_returns_allow_on_plan_approved(workspace: Path) -> None:
    _write_state(workspace / ".harness" / "state.yaml", "c1", "PLAN_APPROVED")
    server = _make_server(workspace)
    _start_server(server)
    try:
        resp = _request(
            server.socket_path,
            "gate.pre_tool_use",
            {"tool": "Edit", "file": "src/foo.py", "change_id": "c1"},
        )
        assert resp["error"] is None
        assert resp["result"] is not None
        assert resp["result"]["decision"] == "allow"
        assert resp["result"]["current_state"] == "PLAN_APPROVED"
    finally:
        server.shutdown()


def test_gate_query_returns_block_on_intent_declared(workspace: Path) -> None:
    _write_state(workspace / ".harness" / "state.yaml", "c1", "INTENT_DECLARED")
    server = _make_server(workspace)
    _start_server(server)
    try:
        resp = _request(
            server.socket_path,
            "gate.pre_tool_use",
            {"tool": "Edit", "file": "src/foo.py", "change_id": "c1"},
        )
        assert resp["error"] is None
        assert resp["result"]["decision"] == "block"
        assert resp["result"]["current_state"] == "INTENT_DECLARED"
    finally:
        server.shutdown()


def test_gate_query_no_active_change_allows(workspace: Path) -> None:
    """No change_id (or unknown change) → ALLOW with "no active change" reason."""
    server = _make_server(workspace)
    _start_server(server)
    try:
        resp = _request(
            server.socket_path,
            "gate.pre_tool_use",
            {"tool": "Edit", "file": "src/foo.py"},  # no change_id
        )
        assert resp["error"] is None
        assert resp["result"]["decision"] == "allow"
        assert "no active change" in resp["result"]["reason"].lower()
    finally:
        server.shutdown()


def test_socket_permissions_0o600(workspace: Path) -> None:
    """AC-9: socket file owner-only RW."""
    server = _make_server(workspace)
    _start_server(server)
    try:
        mode = stat.S_IMODE(server.socket_path.stat().st_mode)
        assert mode == 0o600, f"socket mode 0o{mode:o} ≠ 0o600"
    finally:
        server.shutdown()


def test_brokenpipe_on_client_crash_doesnt_kill_daemon(workspace: Path) -> None:
    """UC-9: client closes connection before reading reply → daemon survives."""
    _write_state(workspace / ".harness" / "state.yaml", "c1", "PLAN_APPROVED")
    server = _make_server(workspace)
    _start_server(server)
    try:
        # First connection: send + close without reading.
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(str(server.socket_path))
        s.sendall(
            encode_request(
                GateQueryRequest(
                    method="gate.pre_tool_use",
                    params={"tool": "Edit", "file": "x", "change_id": "c1"},
                    id="early-quit",
                )
            )
        )
        s.close()  # close before recv — daemon may see BrokenPipeError on write
        time.sleep(0.05)
        # Second connection: must still work.
        resp = _request(server.socket_path, "ping", {})
        assert resp["error"] is None
        assert resp["result"]["version"] == PROTOCOL_VERSION
    finally:
        server.shutdown()


def test_oversized_request_rejected_with_400(workspace: Path) -> None:
    """UC-8: 2MB line → 400 error returned, daemon still alive."""
    server = _make_server(workspace)
    _start_server(server)
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(2.0)
        s.connect(str(server.socket_path))
        # Send 2MB followed by a real ping to verify daemon stays up.
        # Send as one giant garbage line.
        garbage = b"x" * (2 * 1024 * 1024) + b"\n"
        line = b""
        try:
            try:
                s.sendall(garbage)
            except BrokenPipeError:
                # Acceptable: daemon may close mid-send once it sees the cap busted.
                pass
            try:
                line = s.makefile("rb").readline()
            except (TimeoutError, OSError):
                line = b""
        finally:
            s.close()
        # Best-effort: daemon may close conn before whole 2MB lands; accept either
        # explicit 400 or EOF. The defining check is daemon-still-alive below.
        if line:
            resp_obj = json.loads(line.decode())
            assert resp_obj.get("error", {}).get("code") == 400
        # Daemon still alive?
        resp = _request(server.socket_path, "ping", {})
        assert resp["error"] is None
    finally:
        server.shutdown()


def test_oversized_line_rejected_without_parse_attempt(workspace: Path) -> None:
    """UC-8 DoS defense: readline() is capped at MAX_REQUEST_BYTES + 1.

    A malicious 2MiB single line (no newline within the cap) is rejected
    with a 400 envelope before any json.loads() attempt, then the daemon
    closes the connection. Subsequent connections still work — proving
    the per-conn thread didn't leak and the accept loop survived.
    """
    from super_harness.daemon.protocol import MAX_REQUEST_BYTES

    server = _make_server(workspace)
    _start_server(server)
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(2.0)
        s.connect(str(server.socket_path))
        # 2 MiB of `x` bytes — well past MAX_REQUEST_BYTES, NO newline within cap.
        oversized = b"x" * (2 * MAX_REQUEST_BYTES) + b"\n"
        try:
            s.sendall(oversized)
        except BrokenPipeError:
            # Acceptable: daemon may close mid-send once it sees the cap busted.
            pass
        # Best-effort recv of the 400 envelope (daemon may close before we recv).
        try:
            line = s.makefile("rb").readline()
        except (TimeoutError, OSError):
            line = b""
        s.close()
        if line:
            obj = json.loads(line.decode())
            assert obj["error"]["code"] == 400
            assert "exceeds" in obj["error"]["message"].lower()
        # Definitive check: daemon still alive on a fresh connection.
        resp = _request(server.socket_path, "ping", {})
        assert resp["error"] is None
    finally:
        server.shutdown()


def test_version_mismatch_returns_400(workspace: Path) -> None:
    """AC-7: bad protocol version → 400, daemon survives."""
    server = _make_server(workspace)
    _start_server(server)
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(2.0)
        s.connect(str(server.socket_path))
        s.sendall(
            (json.dumps({"version": "9.9.9", "method": "ping", "params": {}, "id": "v"})
             + "\n").encode()
        )
        line = s.makefile("rb").readline()
        s.close()
        obj = json.loads(line.decode())
        assert obj["error"]["code"] == 400
        assert "version" in obj["error"]["message"].lower()
        # Daemon still alive?
        resp = _request(server.socket_path, "ping", {})
        assert resp["error"] is None
    finally:
        server.shutdown()


def test_socket_path_too_long_falls_back_to_tmpdir(tmp_path: Path) -> None:
    """§3.9 risk #8: socket path > OS limit falls back to $TMPDIR/super-harness-<hash>.sock.

    Hash MUST be hashlib.sha256(str(workspace_root.resolve()).encode("utf-8")).hexdigest()[:16].
    """
    # Build a path long enough to bust both Linux (108) and macOS (104) limits.
    deep = tmp_path / ("a" * 50) / ("b" * 50) / ("c" * 50) / ".harness"
    deep.mkdir(parents=True)
    long_socket = deep / "daemon.sock"
    assert len(str(long_socket).encode("utf-8")) > 104

    state_path = deep / "state.yaml"
    events_path = deep / "events.jsonl"
    server = DaemonServer(
        socket_path=long_socket,
        state_path=state_path,
        events_path=events_path,
    )
    _start_server(server)
    try:
        # The effective socket lives under $TMPDIR with deterministic hash.
        workspace_root = deep.parent  # parent of .harness/
        expected_hash = hashlib.sha256(
            str(workspace_root.resolve()).encode("utf-8")
        ).hexdigest()[:16]
        expected = Path(os.environ.get("TMPDIR", "/tmp")) / f"super-harness-{expected_hash}.sock"
        assert server.socket_path == expected
        assert expected.exists()
    finally:
        server.shutdown()
