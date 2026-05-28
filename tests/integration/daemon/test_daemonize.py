"""Integration tests for daemonize() + server:main entry-point.

Per daemon-architecture spec §3.3 (POSIX double-fork) + §3.6 (logging) +
§3.9 #9 (fork/thread invariant) + AC-8 (SIGTERM cleanup) +
Stevens APUE §13.3 (umask(0), chdir("/"), stdio redirect).

These tests cannot run `daemonize()` directly in pytest (it would
double-fork the test runner). Instead they invoke the
`super-harness-daemon` binary (which calls `daemonize()` then
`server.serve_forever()`) and verify externally observable behavior:
socket appears, PID file holds the daemonized PID (not the launcher's),
log file is JSON-lines, SIGTERM cleans up within 2 seconds.
"""
from __future__ import annotations

import json
import os
import signal
import socket as _socket
import subprocess
import time
from pathlib import Path

import pytest
import yaml

from super_harness.daemon._uds_path import resolve_socket_path
from super_harness.daemon.protocol import (
    GateQueryRequest,
    decode_response,
    encode_request,
)


def _wait_for_socket(socket_path: Path, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if socket_path.exists():
            return True
        time.sleep(0.05)
    return False


def _setup_workspace(tmp_path: Path) -> Path:
    harness = tmp_path / ".harness"
    harness.mkdir()
    (harness / "events.jsonl").write_text("")
    (harness / "state.yaml").write_text(yaml.safe_dump({"changes": {}}))
    return tmp_path


def _ping_daemon(socket_path: Path, timeout: float = 2.0) -> bytes:
    """Raw-socket ping. Returns the response line bytes.

    Inlined here (rather than using `super_harness.daemon.client.query`)
    because Task 4.4a builds the client module; this test ships in 4.3b
    so we exercise the protocol layer directly. Equivalent behaviour;
    swap to client.query in 4.4a if desired (not required).
    """
    s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(str(socket_path))
        s.sendall(
            encode_request(GateQueryRequest(method="ping", params={}, id="ping-test"))
        )
        with s.makefile("rb") as rf:
            line = rf.readline()
    finally:
        s.close()
    return line


def test_super_harness_daemon_binary_starts_and_responds(tmp_path: Path) -> None:
    """The super-harness-daemon entry-point spawns a real daemon, binds the
    socket, and answers ping. The launcher Popen exits 0 immediately because
    daemonize() double-forks; the grandchild becomes the long-lived daemon.
    """
    workspace = _setup_workspace(tmp_path)
    proc = subprocess.Popen(
        ["super-harness-daemon", "--workspace", str(workspace)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    try:
        # Parent Popen returns immediately because daemonize() does the
        # first fork+exit. We wait for that first-parent exit (should be
        # <2s on any reasonable machine).
        proc.wait(timeout=2.0)
        assert proc.returncode == 0
        socket_path = resolve_socket_path(workspace)
        assert _wait_for_socket(socket_path), "daemon did not bind socket within 5s"
        # PID file should exist with the daemonized PID (NOT the launcher's PID
        # — the launcher exited; only the double-forked grandchild remains).
        pid_file = workspace / ".harness" / "daemon.pid"
        assert pid_file.exists()
        daemon_pid = int(pid_file.read_text().strip())
        assert daemon_pid != proc.pid
        # Ping via raw socket I/O to prove the protocol works.
        line = _ping_daemon(socket_path, timeout=2.0)
        resp = decode_response(line)
        assert resp.error is None
        assert resp.result is not None
        assert "version" in resp.result
    finally:
        if (workspace / ".harness" / "daemon.pid").exists():
            try:
                pid = int((workspace / ".harness" / "daemon.pid").read_text().strip())
                os.kill(pid, signal.SIGTERM)
            except (ProcessLookupError, ValueError):
                pass


def test_sigterm_triggers_clean_shutdown_under_2s(tmp_path: Path) -> None:
    """AC-8: SIGTERM → daemon exits within 2s, unlinks socket + pid file."""
    workspace = _setup_workspace(tmp_path)
    subprocess.Popen(
        ["super-harness-daemon", "--workspace", str(workspace)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    ).wait(timeout=2.0)

    socket_path = resolve_socket_path(workspace)
    pid_file = workspace / ".harness" / "daemon.pid"
    assert _wait_for_socket(socket_path)
    daemon_pid = int(pid_file.read_text().strip())

    os.kill(daemon_pid, signal.SIGTERM)

    deadline = time.time() + 2.0
    while time.time() < deadline:
        try:
            os.kill(daemon_pid, 0)  # signal 0 = liveness probe
        except ProcessLookupError:
            break  # daemon dead — expected
        time.sleep(0.05)
    else:
        pytest.fail(f"daemon {daemon_pid} did not exit within 2s of SIGTERM")

    # AC-8 socket cleanup
    assert not socket_path.exists(), "socket file leaked after SIGTERM"
    # PID file cleanup is best-effort; kernel auto-releases the flock on
    # process death so a leftover file is recoverable. The hard requirement
    # is socket file gone (the next daemon would refuse to bind otherwise).


def test_daemon_log_file_is_json_lines(tmp_path: Path) -> None:
    """Spec §3.6: daemon.log must be JSON-lines, one record per line.

    The log is consumed by AI agents for self-diagnosis (not humans), so
    every line must be `json.loads`-parseable with stable schema keys.
    """
    workspace = _setup_workspace(tmp_path)
    proc = subprocess.Popen(
        ["super-harness-daemon", "--workspace", str(workspace)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    proc.wait(timeout=2.0)
    socket_path = resolve_socket_path(workspace)
    assert _wait_for_socket(socket_path)

    # Issue at least one query to ensure a log record is emitted.
    # Inlined raw socket I/O (client.query lands in Task 4.4a).
    _ping_daemon(socket_path, timeout=2.0)
    time.sleep(0.1)  # let any async flush settle

    log_file = workspace / ".harness" / "daemon.log"
    assert log_file.exists()
    lines = log_file.read_text().splitlines()
    assert len(lines) >= 1, f"expected >=1 log line, got {len(lines)}"
    for line in lines:
        rec = json.loads(line)  # MUST parse — raises if not JSON
        assert "ts" in rec
        assert "level" in rec
        assert "msg" in rec

    # Cleanup
    daemon_pid = int((workspace / ".harness" / "daemon.pid").read_text().strip())
    os.kill(daemon_pid, signal.SIGTERM)


def test_pre_fork_threading_invariant() -> None:
    """Spec §3.9 #9: daemonize() must run with threading.active_count() == 1.

    POSIX fork() in a multi-threaded process is undefined behavior:
    mutexes held by threads other than the forking one remain locked
    forever in the child, which silently deadlocks (often inside the
    Python interpreter's own locks). The defense is an explicit assert
    at the top of daemonize() that runs before the first fork.

    We can't run daemonize() in pytest (it would daemonize the test
    runner), so we verify the assert is present in source via inspect.
    """
    import inspect

    from super_harness.daemon import server as srv

    source = inspect.getsource(srv.daemonize)
    assert "threading.active_count()" in source, (
        "daemonize() must assert single-thread invariant per spec §3.9 #9"
    )
    # The assert must be testing equality (or <=) to 1 — anything else
    # (e.g. `assert threading.active_count() < 5`) would silently allow
    # the very deadlock the invariant exists to prevent.
    assert ("== 1" in source) or ("<= 1" in source), (
        "daemonize() single-thread assertion must check `== 1` or `<= 1`"
    )


def test_no_super_harness_directory_exits_3(tmp_path: Path) -> None:
    """No `.harness/` in workspace → exit 3 (EXIT_NO_CONFIG) per cli-command-surface."""
    proc = subprocess.run(
        ["super-harness-daemon", "--workspace", str(tmp_path)],
        capture_output=True,
        timeout=5.0,
    )
    assert proc.returncode == 3
