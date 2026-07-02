"""Integration tests for the observer-host lifecycle (design 2026-07-03).

Exercises the real `super-harness-daemon` binary via supervisor: absolute-path
spawn, pidfile-flock liveness, idempotent start, SIGTERM stop, and the
single-instance invariant under concurrent starts (formerly covered by the
deleted test_concurrent_spawn.py — the daemonize pidfile flock still enforces
one live host). No socket.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from super_harness.daemon import supervisor


@pytest.fixture()
def ws(tmp_path: Path) -> Path:
    (tmp_path / ".harness").mkdir()
    yield tmp_path
    supervisor.stop(tmp_path)  # best-effort cleanup


def test_not_running_before_start(ws: Path) -> None:
    assert supervisor.is_running(ws) is False


def test_start_makes_it_live_then_stop(ws: Path) -> None:
    pid = supervisor.ensure_running(ws, wait_seconds=10.0)
    assert pid > 0
    assert supervisor.is_running(ws) is True
    # 2s stop budget preserves the retired daemon's clean-shutdown SLA coverage.
    assert supervisor.stop(ws, wait_seconds=2.0) is True
    assert supervisor.is_running(ws) is False


def test_start_is_idempotent(ws: Path) -> None:
    pid1 = supervisor.ensure_running(ws, wait_seconds=10.0)
    pid2 = supervisor.ensure_running(ws, wait_seconds=10.0)
    assert pid1 == pid2  # second call returns the live host, no respawn


def test_concurrent_starts_yield_one_live_host(ws: Path) -> None:
    # Concurrent ensure_running() calls must converge on ONE live host: the
    # daemonize pidfile flock lets exactly one grandchild survive (losers exit 1),
    # so EVERY caller that returns sees the SAME pid. (Threaded ensure_running,
    # not raw Popen: 'losers exit 1' is not observable through the Popen parent
    # after the double-fork, and identical return pids is the stronger proof.)
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        pids = list(
            ex.map(lambda _: supervisor.ensure_running(ws, wait_seconds=15.0), range(5))
        )
    assert all(p == pids[0] for p in pids), f"expected one live host, got pids {pids}"
    assert pids[0] > 0
    assert supervisor.is_running(ws) is True


def test_observer_binary_is_scripts_dir_sibling() -> None:
    # Resolution is invocation-independent (sysconfig scripts dir), so this holds
    # under `python -m pytest` too — unconditional, non-vacuous. The test venv
    # installs super-harness-daemon, so _observer_binary() must NOT raise.
    import sysconfig

    expected = Path(sysconfig.get_path("scripts")) / "super-harness-daemon"
    resolved = supervisor._observer_binary()
    assert resolved == str(expected)
    assert Path(resolved).is_absolute()
