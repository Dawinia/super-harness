"""Integration tests for daemon-architecture AC-11.

The daemon process MUST NEVER open `.harness/events.jsonl` or
`.harness/state.yaml` in any write mode (`w` / `a` / `+` / `x`).
The single-writer invariant lives in the reducer (state.yaml) and the
event-emitter (events.jsonl); the daemon is reader-only.

Strategy: monkeypatch `builtins.open` AND `io.open` with a tracking
wrapper that records `(path, mode)` of every call, spin up DaemonServer
IN-PROCESS (must be in-process — subprocess inherits unpatched
interpreter), drive 50 `gate.pre_tool_use` queries, then assert no
write-mode opens of the two protected paths.

Notes on coverage limits:
- This test catches `builtins.open` / `io.open` (Python-layer) violations.
  It does NOT catch `os.open()` (raw fd) violations or `os.write()` to
  existing fds. A future `os.open(path, os.O_WRONLY)` would pass this test
  vacuously; the canary below still passes (reads go via io.open). Filed
  as a documented v0.2 gap (complementary os.open tracker).
- The server runs in-process — coverage is "the daemon code paths
  exercised by 50 gate.pre_tool_use queries on a healthy workspace".
"""
from __future__ import annotations

import io
import socket
import threading
import time
from pathlib import Path

import pytest
import yaml

from super_harness.daemon.protocol import (
    GateQueryRequest,
    decode_response,
    encode_request,
)
from super_harness.daemon.server import DaemonServer

# Modes that imply WRITE access per Python docs:
# 'w' truncate-write, 'a' append, 'x' exclusive-create, '+' read-AND-write
_WRITE_MODE_CHARS = frozenset("wax+")


def _is_write_mode(mode: str) -> bool:
    return any(ch in _WRITE_MODE_CHARS for ch in mode)


def _write_state(workspace: Path, change_id: str, current_state: str) -> None:
    state_path = workspace / ".harness" / "state.yaml"
    # Real reducer shape: `changes` map only, NO top-level active_change_id
    # (the reducer never writes it; "active" is derived = first non-terminal).
    state_path.write_text(
        yaml.safe_dump(
            {
                "changes": {
                    change_id: {
                        "change_id": change_id,
                        "current_state": current_state,
                    }
                },
            }
        )
    )


def _start_server(server: DaemonServer) -> threading.Thread:
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
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


def _drive_n_queries(socket_path: Path, n: int) -> None:
    """Send N gate.pre_tool_use queries through real UDS clients."""
    for i in range(n):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(2.0)
        try:
            s.connect(str(socket_path))
            req = GateQueryRequest(
                method="gate.pre_tool_use",
                params={
                    "tool": "Edit",
                    "file": f"src/foo_{i}.py",
                    "change_id": "c1",
                },
                id=f"q{i}",
            )
            s.sendall(encode_request(req))
            # Drain response so the daemon completes the handler.
            line = s.makefile("rb").readline()
            assert line, "daemon closed without responding"
            decode_response(line)
        finally:
            s.close()


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / ".harness").mkdir()
    _write_state(tmp_path, "c1", "PLAN_APPROVED")
    return tmp_path


@pytest.fixture
def tracker(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Install an open() tracker BEFORE DaemonServer is constructed.

    Patches BOTH builtins.open (direct open() calls) AND io.open (what
    pathlib.Path.read_text/.open use — HotState's actual read path).
    Monkeypatching only builtins.open misses pathlib reads because
    pathlib references io.open via the io-module namespace, which the
    builtins-namespace patch does not rebind.

    Returns a list that accumulates `(path_str, mode_str)` tuples.
    Caller drives the daemon, then inspects the list at end of test.
    """
    opens: list[tuple[str, str]] = []
    real_open = open  # original builtin (builtins.open is io.open — same object)

    def tracking_open(file, mode="r", *args, **kwargs):  # type: ignore[no-untyped-def]
        # Normalize path to str — daemon code may pass Path or str.
        opens.append((str(file), mode))
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr("builtins.open", tracking_open)
    monkeypatch.setattr(io, "open", tracking_open)
    return opens


def test_daemon_never_opens_state_yaml_for_write(
    workspace: Path, tracker: list[tuple[str, str]]
) -> None:
    """AC-11 (state.yaml half): no write-mode open() targeting state.yaml."""
    server = DaemonServer(
        socket_path=workspace / ".harness" / "daemon.sock",
        state_path=workspace / ".harness" / "state.yaml",
        events_path=workspace / ".harness" / "events.jsonl",
    )
    _start_server(server)
    try:
        _drive_n_queries(server.socket_path, n=50)
    finally:
        server.shutdown()

    bad = [
        (p, m)
        for p, m in tracker
        if p.endswith("state.yaml") and _is_write_mode(m)
    ]
    assert not bad, (
        f"AC-11 violation: daemon opened state.yaml in write mode: {bad}"
    )


def test_daemon_never_opens_events_jsonl_for_write(
    workspace: Path, tracker: list[tuple[str, str]]
) -> None:
    """AC-11 (events.jsonl half): no write-mode open() targeting events.jsonl.

    Note: in v0.1 the daemon doesn't read events.jsonl at all — this
    test guards against a future regression where someone adds
    "daemon emits an event" code without going through the reducer.
    """
    server = DaemonServer(
        socket_path=workspace / ".harness" / "daemon.sock",
        state_path=workspace / ".harness" / "state.yaml",
        events_path=workspace / ".harness" / "events.jsonl",
    )
    _start_server(server)
    try:
        _drive_n_queries(server.socket_path, n=50)
    finally:
        server.shutdown()

    bad = [
        (p, m)
        for p, m in tracker
        if p.endswith("events.jsonl") and _is_write_mode(m)
    ]
    assert not bad, (
        f"AC-11 violation: daemon opened events.jsonl in write mode: {bad}"
    )


def test_tracker_sanity_state_yaml_was_opened_for_read(
    workspace: Path, tracker: list[tuple[str, str]]
) -> None:
    """Sanity check: the monkeypatch ACTUALLY observes daemon I/O.

    If this test fails it means our tracker is being bypassed (e.g.
    daemon code switched to os.open / a C-level fd). The above two tests
    would then pass vacuously. AC-11 protection ONLY holds if this canary
    stays green.
    """
    server = DaemonServer(
        socket_path=workspace / ".harness" / "daemon.sock",
        state_path=workspace / ".harness" / "state.yaml",
        events_path=workspace / ".harness" / "events.jsonl",
    )
    _start_server(server)
    try:
        _drive_n_queries(server.socket_path, n=5)
    finally:
        server.shutdown()

    state_reads = [
        (p, m)
        for p, m in tracker
        if p.endswith("state.yaml") and not _is_write_mode(m)
    ]
    assert state_reads, (
        f"tracker observed 0 reads of state.yaml — monkeypatch likely "
        f"bypassed. All observed opens: {tracker[:20]}"
    )
