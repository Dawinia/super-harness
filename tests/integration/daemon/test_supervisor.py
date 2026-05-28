"""Integration tests for daemon.supervisor per daemon-architecture §3.5 Layer 2.

These tests cover the hot-path fire-and-forget split — the iteration-2
critical decision: `gate_pre_tool_use()` MUST return in <50ms even when
the daemon is down (AC-2), spawning the daemon asynchronously and writing
a fallback audit line (AC-10). `ensure_running()` is the foreground CLI
path that DOES block until the socket appears.

Some tests spawn a real `super-harness-daemon` subprocess; tests are
skipped if the entry-point is not on PATH (e.g. local dev forgot to
`pip install -e .` after Task 4.4c lands the entry-point). The Phase 4
TDD step in Task 4.4c re-installs before running these tests.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from super_harness.daemon import supervisor
from super_harness.daemon._uds_path import resolve_socket_path


def _has_daemon_binary() -> bool:
    return shutil.which("super-harness-daemon") is not None


pytestmark = pytest.mark.skipif(
    not _has_daemon_binary(),
    reason="super-harness-daemon entry-point not installed; run `pip install -e .`",
)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / ".harness").mkdir()
    return tmp_path


def _write_state(workspace: Path, change_id: str, current_state: str) -> None:
    state_path = workspace / ".harness" / "state.yaml"
    # Real reducer shape: `changes` map only, NO top-level active_change_id
    # (the reducer never writes it; "active" is derived = first non-terminal).
    state_path.write_text(
        yaml.safe_dump(
            {"changes": {change_id: {"change_id": change_id,
                                     "current_state": current_state}}}
        )
    )


def _today_log(workspace: Path) -> Path:
    date = datetime.now(timezone.utc).date().isoformat()
    return workspace / ".harness" / "operation-logs" / f"daemon-fallback-{date}.log"


def _kill_daemon_if_running(workspace: Path) -> None:
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


def test_gate_pre_tool_use_returns_immediately_when_daemon_down(
    workspace: Path,
) -> None:
    """AC-2: total elapsed < 50ms when daemon is not running.

    The function must NOT block on daemon spawn — Popen is fire-and-forget,
    audit line write is the only mandatory side effect.
    """
    _write_state(workspace, "c1", "PLAN_APPROVED")
    t0 = time.perf_counter()
    decision, reason = supervisor.gate_pre_tool_use(
        workspace, tool="Edit", file="src/foo.py", change_id="c1"
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000
    try:
        assert decision == "allow"
        assert "daemon" in reason.lower()  # fallback reason mentions daemon
        # Budget: 50ms total. Generous margin for CI noise — local typically ~5ms.
        assert elapsed_ms < 50.0, (
            f"gate_pre_tool_use took {elapsed_ms:.1f}ms; AC-2 budget is <50ms"
        )
    finally:
        _kill_daemon_if_running(workspace)


def test_gate_pre_tool_use_writes_audit_log_on_fallback(workspace: Path) -> None:
    """AC-10: every fail-safe ALLOW writes one JSON line to daemon-fallback-<UTC>.log."""
    log = _today_log(workspace)
    assert not log.exists()
    try:
        supervisor.gate_pre_tool_use(
            workspace, tool="Edit", file="src/foo.py", change_id="c1"
        )
        assert log.exists(), f"missing audit log: {log}"
        lines = log.read_text().strip().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["method"] == "gate.pre_tool_use"
        assert record["params"] == {"tool": "Edit", "file": "src/foo.py",
                                    "change_id": "c1"}
        assert "ts" in record
        assert "reason" in record
    finally:
        _kill_daemon_if_running(workspace)


def test_audit_log_appends_not_overwrites(workspace: Path) -> None:
    """Two fallback calls produce two lines, not overwrite."""
    log = _today_log(workspace)
    try:
        supervisor.gate_pre_tool_use(
            workspace, tool="Edit", file="a.py", change_id="c1"
        )
        # Force daemon down for second call too — kill anything we may have spawned.
        _kill_daemon_if_running(workspace)
        sock = resolve_socket_path(workspace)
        if sock.exists():
            sock.unlink()
        supervisor.gate_pre_tool_use(
            workspace, tool="Write", file="b.py", change_id="c1"
        )
        lines = log.read_text().strip().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["params"]["file"] == "a.py"
        assert json.loads(lines[1])["params"]["file"] == "b.py"
    finally:
        _kill_daemon_if_running(workspace)


def test_gate_pre_tool_use_respawns_on_version_mismatch(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """UC-6: 400 envelope with 'version' → SIGTERM stale daemon + respawn + ALLOW.

    Strategy: monkeypatch `client.query` to return a synthetic version-mismatch
    error envelope; monkeypatch `subprocess.Popen` AND `os.kill` to record
    invocations. Assert that the supervisor:
    (a) signaled the stale daemon with SIGTERM (regression guard against the
        round-3 "no-op respawn against a live old daemon" finding),
    (b) returned ALLOW with a reason containing "respawning",
    (c) called Popen with the `super-harness-daemon` argv,
    (d) wrote the fallback audit line.

    Pre-arrange a stale PID file with PID=99999 so _signal_stale_daemon has
    something to read (the real os.kill is monkeypatched so the PID can be
    arbitrary).
    """
    import signal as _signal

    spawned: list[list[str]] = []
    signals: list[tuple[int, int]] = []  # (pid, signum)

    def fake_popen(argv, *args, **kwargs):  # type: ignore[no-untyped-def]
        spawned.append(list(argv))
        class _StubProc:
            pid = 12345
        return _StubProc()

    def fake_kill(pid, sig):  # type: ignore[no-untyped-def]
        signals.append((pid, sig))

    def fake_query(socket_path, *, method, params, timeout=2.0, request_id=None):
        return {
            "id": request_id,
            "result": None,
            "error": {"code": 400, "message": "ProtocolVersionMismatch: got '9.9.9'"},
        }

    monkeypatch.setattr("super_harness.daemon.supervisor.subprocess.Popen", fake_popen)
    monkeypatch.setattr("super_harness.daemon.supervisor.os.kill", fake_kill)
    monkeypatch.setattr("super_harness.daemon.supervisor.query", fake_query)

    # Stale PID file (real daemon would be PID 99999, but kill is faked)
    pid_file = workspace / ".harness" / "daemon.pid"
    pid_file.write_text("99999\n")

    _write_state(workspace, "c1", "PLAN_APPROVED")
    decision, reason = supervisor.gate_pre_tool_use(
        workspace, tool="Edit", file="src/foo.py", change_id="c1"
    )

    # (a) SIGTERM was sent to the stale daemon (regression guard, round-3 finding)
    assert (99999, _signal.SIGTERM) in signals, (
        f"expected SIGTERM(99999) before respawn; got: {signals}. "
        "Without SIGTERM, the new spawn loses the PID flock race + exits 1, "
        "and the NEXT call hits the same stale daemon → infinite respawn loop."
    )
    # (b) ALLOW + respawning reason
    assert decision == "allow"
    assert "respawning" in reason.lower()
    assert "version" in reason.lower()
    # (c) Popen called with daemon entry-point argv
    assert any(argv and argv[0] == "super-harness-daemon" for argv in spawned), (
        f"expected super-harness-daemon spawn, got: {spawned}"
    )
    # (d) Audit log line written
    log = _today_log(workspace)
    assert log.exists()
    record = json.loads(log.read_text().strip().splitlines()[-1])
    assert record["method"] == "gate.pre_tool_use"
    assert "version" in record["reason"].lower()


def test_ensure_running_blocks_until_socket_appears(workspace: Path) -> None:
    """CLI-path: ensure_running() returns only once daemon is reachable."""
    try:
        pid = supervisor.ensure_running(workspace, wait_seconds=5.0)
        assert isinstance(pid, int) and pid > 0
        # PID file exists, socket exists, ping works.
        assert (workspace / ".harness" / "daemon.pid").exists()
        assert resolve_socket_path(workspace).exists()
        # Sanity: process is alive.
        os.kill(pid, 0)
    finally:
        _kill_daemon_if_running(workspace)


def test_ensure_running_returns_existing_pid_when_already_running(
    workspace: Path,
) -> None:
    try:
        pid1 = supervisor.ensure_running(workspace, wait_seconds=5.0)
        pid2 = supervisor.ensure_running(workspace, wait_seconds=5.0)
        assert pid1 == pid2
    finally:
        _kill_daemon_if_running(workspace)


def test_gate_pre_tool_use_uses_daemon_when_running(workspace: Path) -> None:
    """When daemon is up, decision comes from server (not fallback).

    Verify by setting state.yaml → PLAN_APPROVED and asserting the decision
    reason matches the server's PLAN_APPROVED reason (not the supervisor's
    "daemon starting; first call permissive" fallback string).
    """
    _write_state(workspace, "c1", "PLAN_APPROVED")
    try:
        supervisor.ensure_running(workspace, wait_seconds=5.0)
        decision, reason = supervisor.gate_pre_tool_use(
            workspace, tool="Edit", file="src/foo.py", change_id="c1"
        )
        assert decision == "allow"
        # Server reason is "PLAN_APPROVED"; fallback reason starts with "daemon".
        assert "PLAN_APPROVED" in reason
        # And the fallback audit log was NOT touched.
        assert not _today_log(workspace).exists()
    finally:
        _kill_daemon_if_running(workspace)


def test_gate_pre_tool_use_blocks_when_daemon_says_block(workspace: Path) -> None:
    _write_state(workspace, "c1", "AWAITING_PLAN_REVIEW")
    try:
        supervisor.ensure_running(workspace, wait_seconds=5.0)
        decision, reason = supervisor.gate_pre_tool_use(
            workspace, tool="Edit", file="src/foo.py", change_id="c1"
        )
        assert decision == "block"
        assert "AWAITING_PLAN_REVIEW" in reason
    finally:
        _kill_daemon_if_running(workspace)
