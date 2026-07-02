"""Shared helpers for the daemon integration tests.

Consolidated here to prevent the copies from drifting (an orphan-leak bug once
lived in one copy of the daemon-killer but not another). Plain functions, not
fixtures — they take explicit args (a server / a workspace) per call site.
"""
from __future__ import annotations

import os
import signal
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

from super_harness.daemon.server import DaemonServer

# Hot-path query timeout for hook subprocesses in tests: 5s overrides the
# production 200ms AC-2 budget so CI-load variance (subprocess cold-start +
# import time + socket connect) cannot eat the entire window before the daemon
# query lands. Without this, `test_hook_entry_exits_1_on_block` occasionally
# fail-opened (exit 0) instead of blocking (exit 1) under heavy CI parallelism
# — see OPEN-ITEMS #4 (daemon readiness determinism). Applied automatically to
# every test in this directory via the autouse fixture below; child subprocess
# inherits the env var.
_HOOK_QUERY_TIMEOUT_FOR_TESTS = "5.0"


@pytest.fixture(autouse=True)
def _hook_query_timeout_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv(
        "SUPER_HARNESS_HOOK_QUERY_TIMEOUT", _HOOK_QUERY_TIMEOUT_FOR_TESTS
    )
    yield


def write_state(workspace: Path, change_id: str, current_state: str) -> None:
    state_path = workspace / ".harness" / "state.yaml"
    # Real reducer shape: `changes` map only, NO top-level active_change_id
    # (the reducer never writes it; "active" is derived = most recently active non-terminal).
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


def start_server(server: DaemonServer) -> threading.Thread:
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


def kill_daemon(workspace: Path) -> None:
    pid_file = workspace / ".harness" / "daemon.pid"
    # Hot-path tests fire-and-forget a real daemon that may still be
    # double-forking when this teardown runs (PID file absent), or the file may
    # hold a stale DEAD pid from a prior daemon while a freshly-spawned one is
    # still booting (the appends-not-overwrites test spawns two). Poll until the
    # file names a LIVE process, then SIGTERM it — otherwise we kill a corpse
    # and leave an orphan (cwd=/, survives workspace cleanup, holds the fallback
    # socket forever). The daemon normally registers in <1s.
    deadline = time.monotonic() + 2.0
    pid: int | None = None
    while time.monotonic() < deadline:
        if pid_file.exists():
            try:
                candidate = int(pid_file.read_text().strip())
                os.kill(candidate, 0)  # liveness probe; raises if dead
                pid = candidate
                break
            except (ValueError, ProcessLookupError, OSError):
                pass
        time.sleep(0.05)
    if pid is None:
        return
    try:
        os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
                time.sleep(0.05)
            except ProcessLookupError:
                break
    except (ValueError, ProcessLookupError):
        pass
