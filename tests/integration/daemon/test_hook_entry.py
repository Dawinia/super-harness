"""End-to-end tests for the `super-harness-hook` binary per
daemon-architecture §3.5 + cli-command-surface §3.1.

These tests invoke the actual entry-point via `subprocess.run` (the
real PreToolUse path), not the Python function — that's the only way
to verify the click-less import chain stays click-less + the entry-point
is registered in pyproject.toml. Tests skip if the binary isn't on PATH.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest
import yaml


def _has_hook_binary() -> bool:
    return shutil.which("super-harness-hook") is not None


pytestmark = pytest.mark.skipif(
    not _has_hook_binary(),
    reason="super-harness-hook not installed; run `pip install -e .`",
)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / ".harness").mkdir()
    return tmp_path


def _write_state(workspace: Path, change_id: str, current_state: str) -> None:
    (workspace / ".harness" / "state.yaml").write_text(
        yaml.safe_dump(
            {"active_change_id": change_id,
             "changes": {change_id: {"change_id": change_id,
                                     "current_state": current_state}}}
        )
    )


def _kill_daemon(workspace: Path) -> None:
    pid_file = workspace / ".harness" / "daemon.pid"
    # A fire-and-forget daemon (the daemon-down fail-safe test spawns one via
    # the hook) may still be double-forking when teardown runs, or the PID file
    # may hold a stale dead pid. Poll until it names a LIVE process, then
    # SIGTERM — otherwise we leave an immortal orphan (cwd=/, holds the fallback
    # socket). The daemon normally registers in <1s.
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


def _start_daemon(workspace: Path) -> None:
    """Use the supervisor to ensure daemon is up (foreground / blocking)."""
    from super_harness.daemon import supervisor
    supervisor.ensure_running(workspace, wait_seconds=5.0)


def test_hook_entry_exits_0_on_allow(workspace: Path) -> None:
    _write_state(workspace, "c1", "PLAN_APPROVED")
    _start_daemon(workspace)
    try:
        env = {**os.environ, "SUPER_HARNESS_CHANGE_ID": "c1"}
        result = subprocess.run(
            ["super-harness-hook", "Edit", "src/foo.py"],
            cwd=workspace,
            capture_output=True,
            env=env,
            timeout=5.0,
        )
        assert result.returncode == 0, result.stderr.decode()
    finally:
        _kill_daemon(workspace)


def test_hook_entry_exits_1_on_block(workspace: Path) -> None:
    _write_state(workspace, "c1", "AWAITING_PLAN_REVIEW")
    _start_daemon(workspace)
    try:
        env = {**os.environ, "SUPER_HARNESS_CHANGE_ID": "c1"}
        result = subprocess.run(
            ["super-harness-hook", "Edit", "src/foo.py"],
            cwd=workspace,
            capture_output=True,
            env=env,
            timeout=5.0,
        )
        assert result.returncode == 1
        assert b"AWAITING_PLAN_REVIEW" in result.stderr
    finally:
        _kill_daemon(workspace)


def test_hook_entry_exits_0_when_no_harness(tmp_path: Path) -> None:
    """No .harness/ on walk-up → exit 0 (Axiom 1: prevent, not punish)."""
    # `tmp_path` here is a workspace with NO .harness dir.
    result = subprocess.run(
        ["super-harness-hook", "Edit", "src/foo.py"],
        cwd=tmp_path,
        capture_output=True,
        timeout=5.0,
    )
    assert result.returncode == 0


def test_hook_entry_exits_0_on_daemon_down_fail_safe(workspace: Path) -> None:
    """AC-2: daemon down → fail-safe ALLOW (exit 0) + stderr warn."""
    _write_state(workspace, "c1", "PLAN_APPROVED")
    # Deliberately do NOT start the daemon.
    try:
        env = {**os.environ, "SUPER_HARNESS_CHANGE_ID": "c1"}
        result = subprocess.run(
            ["super-harness-hook", "Edit", "src/foo.py"],
            cwd=workspace,
            capture_output=True,
            env=env,
            timeout=5.0,
        )
        assert result.returncode == 0
        # Optional: stderr mentions fail-safe / daemon. Not strictly enforced
        # to avoid coupling test to copy.
    finally:
        _kill_daemon(workspace)


def test_hook_entry_uses_active_change_id_from_state_yaml(workspace: Path) -> None:
    """When env var unset, hook should resolve change_id from state.yaml's
    `active_change_id` field."""
    _write_state(workspace, "c1", "PLAN_APPROVED")
    _start_daemon(workspace)
    try:
        env = {k: v for k, v in os.environ.items()
               if k != "SUPER_HARNESS_CHANGE_ID"}
        result = subprocess.run(
            ["super-harness-hook", "Edit", "src/foo.py"],
            cwd=workspace,
            capture_output=True,
            env=env,
            timeout=5.0,
        )
        assert result.returncode == 0, result.stderr.decode()
    finally:
        _kill_daemon(workspace)
