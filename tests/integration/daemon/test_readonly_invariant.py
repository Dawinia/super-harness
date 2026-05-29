"""Integration tests for daemon-architecture AC-11.

The daemon process MUST NEVER open `.harness/events.jsonl` or
`.harness/state.yaml` in any write mode (`w` / `a` / `+` / `x`).
The single-writer invariant lives in the reducer (state.yaml) and the
event-emitter (events.jsonl); the daemon is reader-only.

Strategy: install a process-global `open` audit hook (sys.addaudithook)
that records `(path, mode)` of every open, spin up DaemonServer IN-PROCESS
(must be in-process -- a subprocess daemon wouldn't carry this process's
audit hook), drive 50 `gate.pre_tool_use` queries, then assert no
write-mode opens of the two protected paths. The audit event fires at the C
level for builtins.open / io.open / pathlib reads alike, so this works
identically on Python 3.10-3.13 (runtime monkeypatching of io.open misses
pathlib reads on 3.10, where pathlib caches io.open at import time).

Notes on coverage limits:
- This test catches Python-layer opens (builtins.open / io.open / pathlib).
  It does NOT catch `os.open()` (raw fd) violations or `os.write()` to
  existing fds -- the `open` audit event reports mode=None for os.open, which
  this tracker filters out. A future `os.open(path, os.O_WRONLY)` would pass
  vacuously; the canary below still passes (reads go via pathlib). Filed as a
  documented v0.2 gap (complementary os.open / audit-flags tracker).
- The server runs in-process -- coverage is "the daemon code paths
  exercised by 50 gate.pre_tool_use queries on a healthy workspace".
"""
from __future__ import annotations

import socket
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from super_harness.daemon.protocol import (
    GateQueryRequest,
    decode_response,
    encode_request,
)
from super_harness.daemon.server import DaemonServer
from tests.integration.daemon.conftest import start_server, write_state

# Modes that imply WRITE access per Python docs:
# 'w' truncate-write, 'a' append, 'x' exclusive-create, '+' read-AND-write
_WRITE_MODE_CHARS = frozenset("wax+")


def _is_write_mode(mode: str) -> bool:
    return any(ch in _WRITE_MODE_CHARS for ch in mode)


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
    write_state(tmp_path, "c1", "PLAN_APPROVED")
    return tmp_path


# --- open() tracking via a process-global audit hook -----------------------
# We use sys.addaudithook (the `open` audit event) rather than monkeypatching
# builtins.open / io.open. The audit event fires at the C level for EVERY open
# -- builtins.open, io.open, AND pathlib.Path.read_text -- identically on Python
# 3.10-3.13. Monkeypatching io.open does NOT work on 3.10: pathlib there caches
# io.open into its internal `_accessor` at import time, so a runtime patch is
# bypassed and the tracker observes 0 reads (the original cross-version bug).
# Audit hooks are permanent (can't be removed), so install once + gate recording.
_AUDIT_OPENS: list[tuple[str, str]] = []
_AUDIT_RECORDING = False
_AUDIT_HOOK_INSTALLED = False


def _install_audit_hook_once() -> None:
    global _AUDIT_HOOK_INSTALLED
    if _AUDIT_HOOK_INSTALLED:
        return

    def _hook(event: str, args: tuple[object, ...]) -> None:
        # `open` event args are (path, mode, flags). mode is a str for
        # builtins/io/pathlib opens; None for os.open (raw fd) -- the latter is
        # filtered out here (documented v0.2 gap, same as before).
        if not _AUDIT_RECORDING or event != "open":
            return
        path, mode = args[0], args[1]
        if path is not None and isinstance(mode, str):
            _AUDIT_OPENS.append((str(path), mode))

    sys.addaudithook(_hook)
    _AUDIT_HOOK_INSTALLED = True


@pytest.fixture
def tracker() -> Iterator[list[tuple[str, str]]]:
    """Record every `open` as (path, mode) via a process-global audit hook.

    The `open` audit event fires at the C level for builtins.open, io.open, and
    pathlib reads alike -- works on Python 3.10-3.13 (runtime monkeypatching of
    io.open misses pathlib reads on 3.10, where pathlib caches io.open at import
    time). os.open (raw fd) is still NOT caught -- a documented v0.2 gap.

    Returns a list that accumulates `(path_str, mode_str)` tuples; the caller
    drives the daemon, then inspects the list at end of test.
    """
    _install_audit_hook_once()
    global _AUDIT_RECORDING
    _AUDIT_OPENS.clear()
    _AUDIT_RECORDING = True
    try:
        yield _AUDIT_OPENS
    finally:
        _AUDIT_RECORDING = False


def test_daemon_never_opens_state_yaml_for_write(
    workspace: Path, tracker: list[tuple[str, str]]
) -> None:
    """AC-11 (state.yaml half): no write-mode open() targeting state.yaml."""
    server = DaemonServer(
        workspace_root=workspace,
        socket_path=workspace / ".harness" / "daemon.sock",
        state_path=workspace / ".harness" / "state.yaml",
        events_path=workspace / ".harness" / "events.jsonl",
    )
    start_server(server)
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

    Note: in v0.1 the daemon doesn't read events.jsonl at all -- this
    test guards against a future regression where someone adds
    "daemon emits an event" code without going through the reducer.
    """
    server = DaemonServer(
        workspace_root=workspace,
        socket_path=workspace / ".harness" / "daemon.sock",
        state_path=workspace / ".harness" / "state.yaml",
        events_path=workspace / ".harness" / "events.jsonl",
    )
    start_server(server)
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
    """Sanity check: the audit hook ACTUALLY observes daemon I/O.

    If this test fails it means our tracker is being bypassed (e.g. daemon
    code switched to os.open / a raw C-level fd that the `open` audit event
    reports with mode=None). The above two tests would then pass vacuously.
    AC-11 protection ONLY holds if this canary stays green.
    """
    server = DaemonServer(
        workspace_root=workspace,
        socket_path=workspace / ".harness" / "daemon.sock",
        state_path=workspace / ".harness" / "state.yaml",
        events_path=workspace / ".harness" / "events.jsonl",
    )
    start_server(server)
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
        f"tracker observed 0 reads of state.yaml -- monkeypatch likely "
        f"bypassed. All observed opens: {tracker[:20]}"
    )
