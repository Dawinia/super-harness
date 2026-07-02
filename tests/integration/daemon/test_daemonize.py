"""Integration tests for daemonize() + the observer-host entry-point.

Per daemon-architecture §3.3 (POSIX double-fork) + §3.9 #9 (fork/thread
invariant). Post-demotion (design 2026-07-03) the process is a pure observer
host — no UDS socket, no ping — so the socket-liveness / round-trip coverage
moved to `test_observer_host.py` (flock liveness via `supervisor`). What remains
here is source-level structural coverage of `daemonize` plus the missing-`.harness`
exit-3 contract, which don't require spawning a long-lived process.
"""
from __future__ import annotations

import subprocess

from super_harness.daemon import server as srv


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


def test_no_super_harness_directory_exits_3(tmp_path) -> None:
    """No `.harness/` in workspace → exit 3 (EXIT_NO_CONFIG) per cli-command-surface."""
    proc = subprocess.run(
        ["super-harness-daemon", "--workspace", str(tmp_path)],
        capture_output=True,
        timeout=5.0,
    )
    assert proc.returncode == 3
