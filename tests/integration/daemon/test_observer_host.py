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


def test_stale_pidfile_under_concurrent_probe_processes_reads_not_running(
    ws: Path,
) -> None:
    # A stale (unheld) pidfile persists after any stop()/crash — is_running must
    # report False even under CONCURRENT probes. The real concurrency here is
    # separate CLI PROCESSES (`observe start`/`status`), so probe from separate
    # processes (POSIX flock semantics are cross-process; same-process thread flock
    # is not a real code path and has platform quirks). The bug this guards: an
    # exclusive (LOCK_EX) probe makes two racing probe PROCESSES conflict with each
    # other on an unheld file — one acquires, the other sees BlockingIOError and
    # falsely reports "running" when nobody holds the lock → `observe start`
    # no-ops and the host never comes up. A shared (LOCK_SH) probe is immune
    # (shared requests are mutually compatible across processes). No host is spawned.
    import concurrent.futures
    import subprocess
    import sys

    (ws / ".harness" / "daemon.pid").write_text("999999\n")  # dead/nonexistent pid
    assert supervisor.is_running(ws) is False  # serial baseline

    probe = (
        "import sys; from pathlib import Path; "
        "from super_harness.daemon import supervisor; "
        "sys.exit(0 if supervisor.is_running(Path(sys.argv[1])) else 1)"
    )

    def probe_process(_: int) -> int:
        return subprocess.run(
            [sys.executable, "-c", probe, str(ws)], capture_output=True
        ).returncode  # 0 == is_running True (false positive), 1 == not running

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as ex:
        codes = list(ex.map(probe_process, range(60)))
    assert all(code == 1 for code in codes), (
        "is_running false-positived on a stale/unheld pidfile under concurrent probe "
        "processes — the liveness probe must use LOCK_SH, not LOCK_EX"
    )


def test_observer_binary_is_scripts_dir_sibling() -> None:
    # Resolution is invocation-independent (sysconfig scripts dir), so this holds
    # under `python -m pytest` too — unconditional, non-vacuous. The test venv
    # installs super-harness-daemon, so _observer_binary() must NOT raise.
    import sysconfig

    expected = Path(sysconfig.get_path("scripts")) / "super-harness-daemon"
    resolved = supervisor._observer_binary()
    assert resolved == str(expected)
    assert Path(resolved).is_absolute()


def test_ensure_running_never_returns_zero_from_half_written_pidfile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Regression (#68 code review): the already-running fast path must NOT return
    # a bare _read_pid() — is_running flips True the instant the flock is held,
    # which in daemonize() PRECEDES ftruncate+write(pid), so _read_pid can read 0
    # from a half-written pidfile. ensure_running must poll until _read_pid > 0.
    # Deterministic via monkeypatch: is_running is already True (no spawn), and
    # _read_pid returns 0 twice (half-written) before the real pid lands.
    from pathlib import Path

    from super_harness.daemon import supervisor as sup

    monkeypatch.setattr(sup, "is_running", lambda root: True)
    pids = iter([0, 0, 4242])
    monkeypatch.setattr(sup, "_read_pid", lambda root, **k: next(pids))
    assert sup.ensure_running(Path("/nonexistent"), wait_seconds=5.0) == 4242
