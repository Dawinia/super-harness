"""Unit tests for `super-harness daemon {start,stop,status}` subcommands.

Tests use CliRunner against the click group; each test stands up an
isolated tmp workspace + tears down any spawned daemon in teardown.
Tests are skipped when `super-harness-daemon` isn't on PATH (Task 4.4c
gates installation).

`status --json` schema is asserted against the cli-command-surface
§2.3.X contract — those keys are load-bearing for downstream consumers
(CI dashboards, `super-harness init` self-check).
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner

from super_harness.cli import main as cli_main
from super_harness.daemon._uds_path import resolve_socket_path


def _has_daemon_binary() -> bool:
    return shutil.which("super-harness-daemon") is not None


pytestmark = pytest.mark.skipif(
    not _has_daemon_binary(),
    reason="super-harness-daemon entry-point not installed",
)

# Foreground daemon-start socket-wait budget for tests: 30s overrides the
# production 5s ceiling so a transient spawn→boot→bind stall under heavy CI
# parallelism (the self-host verification.yaml runs pytest + ruff + mypy 4-way
# in parallel) can't push `daemon start` past the window → RuntimeError → exit 1
# → flaky test_daemon_start_idempotent. Mirrors the hot-path widening in
# tests/integration/daemon/conftest.py:30-38 (SUPER_HARNESS_HOOK_QUERY_TIMEOUT);
# same class as OPEN-ITEM #4 (daemon readiness determinism). The two
# SUPER_HARNESS_DAEMON_START_TIMEOUT-specific tests below override this via their
# own monkeypatch.set/delenv.
_DAEMON_START_TIMEOUT_FOR_TESTS = "30"


@pytest.fixture(autouse=True)
def _daemon_start_timeout_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv(
        "SUPER_HARNESS_DAEMON_START_TIMEOUT", _DAEMON_START_TIMEOUT_FOR_TESTS
    )
    yield


@pytest.fixture
def workspace(tmp_path: Path) -> Iterator[Path]:
    (tmp_path / ".harness").mkdir()
    yield tmp_path
    # Teardown: kill any daemon we spawned.
    pid_file = tmp_path / ".harness" / "daemon.pid"
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
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


def test_daemon_start_starts_daemon(workspace: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli_main, ["--workspace", str(workspace), "daemon", "start"]
    )
    assert result.exit_code == 0, result.output
    assert (workspace / ".harness" / "daemon.pid").exists()
    assert resolve_socket_path(workspace).exists()


def test_daemon_start_idempotent(workspace: Path) -> None:
    runner = CliRunner()
    r1 = runner.invoke(cli_main, ["--workspace", str(workspace), "daemon", "start"])
    r2 = runner.invoke(cli_main, ["--workspace", str(workspace), "daemon", "start"])
    assert r1.exit_code == 0
    assert r2.exit_code == 0


def test_daemon_start_honors_timeout_env_var(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`daemon start` reads SUPER_HARNESS_DAEMON_START_TIMEOUT and threads it
    into supervisor.ensure_running's socket-wait budget.

    This is the foreground-CLI sibling of the hot-path SUPER_HARNESS_HOOK_QUERY_TIMEOUT
    seam (supervisor.gate_pre_tool_use): on a loaded CI runner the 4-way-parallel
    verification (pytest + ruff + mypy) can transiently stall a daemon's
    spawn→boot→bind past the production 5s ceiling, raising RuntimeError → exit 1
    and flaking test_daemon_start_idempotent. The env var lets tests widen the
    window without changing the production default (see OPEN-ITEMS daemon-readiness
    determinism, same class as OPEN-ITEM #4)."""
    captured: dict[str, float] = {}

    def fake_ensure_running(root: Path, *, wait_seconds: float = 5.0) -> int:
        captured["wait_seconds"] = wait_seconds
        return 4321

    monkeypatch.setattr(
        "super_harness.cli.daemon.supervisor.ensure_running", fake_ensure_running
    )
    monkeypatch.setenv("SUPER_HARNESS_DAEMON_START_TIMEOUT", "30")
    runner = CliRunner()
    result = runner.invoke(
        cli_main, ["--workspace", str(workspace), "daemon", "start"]
    )
    assert result.exit_code == 0, result.output
    assert captured["wait_seconds"] == 30.0


def test_daemon_start_timeout_defaults_to_5s(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No env var → ensure_running gets the production 5.0s default unchanged."""
    captured: dict[str, float] = {}

    def fake_ensure_running(root: Path, *, wait_seconds: float = -1.0) -> int:
        captured["wait_seconds"] = wait_seconds
        return 4321

    monkeypatch.setattr(
        "super_harness.cli.daemon.supervisor.ensure_running", fake_ensure_running
    )
    monkeypatch.delenv("SUPER_HARNESS_DAEMON_START_TIMEOUT", raising=False)
    runner = CliRunner()
    result = runner.invoke(
        cli_main, ["--workspace", str(workspace), "daemon", "start"]
    )
    assert result.exit_code == 0, result.output
    assert captured["wait_seconds"] == 5.0


def test_daemon_start_exits_3_when_no_harness(tmp_path: Path) -> None:
    """No .harness/ → exit 3 (EXIT_NO_CONFIG) per §2.3.X."""
    runner = CliRunner()
    # tmp_path has no .harness dir.
    result = runner.invoke(cli_main, ["--workspace", str(tmp_path), "daemon", "start"])
    assert result.exit_code == 3, result.output


def test_daemon_status_reports_stopped_when_not_running(workspace: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli_main, ["--workspace", str(workspace), "daemon", "status"]
    )
    assert result.exit_code == 1, result.output
    assert "not running" in result.output.lower() or "stopped" in result.output.lower()


def test_daemon_status_reports_running_with_pid(workspace: Path) -> None:
    runner = CliRunner()
    runner.invoke(cli_main, ["--workspace", str(workspace), "daemon", "start"])
    result = runner.invoke(
        cli_main, ["--workspace", str(workspace), "daemon", "status"]
    )
    assert result.exit_code == 0, result.output
    assert "running" in result.output.lower()


def test_daemon_status_json_envelope_matches_schema(workspace: Path) -> None:
    """cli-command-surface §2.3.X schema: command/exit_code/data{running,
    stale_pid, pid, protocol_version, daemon_version, uptime_seconds,
    socket_path}."""
    runner = CliRunner()
    runner.invoke(cli_main, ["--workspace", str(workspace), "daemon", "start"])
    result = runner.invoke(
        cli_main, ["--json", "--workspace", str(workspace), "daemon", "status"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["command"] == "daemon status"
    assert payload["exit_code"] == 0
    data = payload["data"]
    assert data["running"] is True
    assert data["stale_pid"] is False
    assert isinstance(data["pid"], int) and data["pid"] > 0
    assert data["protocol_version"] == "1"
    assert isinstance(data["daemon_version"], str)
    assert isinstance(data["uptime_seconds"], (int, float))
    assert data["socket_path"] == str(resolve_socket_path(workspace))


def test_daemon_stop_cleans_socket_and_pid_file(workspace: Path) -> None:
    """AC-6: stop removes the socket + PID file."""
    runner = CliRunner()
    runner.invoke(cli_main, ["--workspace", str(workspace), "daemon", "start"])
    assert resolve_socket_path(workspace).exists()
    assert (workspace / ".harness" / "daemon.pid").exists()
    result = runner.invoke(
        cli_main, ["--workspace", str(workspace), "daemon", "stop"]
    )
    assert result.exit_code == 0, result.output
    # AC-8: <2s for socket cleanup.
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if not resolve_socket_path(workspace).exists():
            break
        time.sleep(0.05)
    assert not resolve_socket_path(workspace).exists()


def test_daemon_stop_returns_1_when_not_running(workspace: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli_main, ["--workspace", str(workspace), "daemon", "stop"]
    )
    assert result.exit_code == 1


def _dead_pid() -> int:
    """A PID guaranteed dead: spawn a trivial child and reap it."""
    proc = subprocess.Popen([sys.executable, "-c", ""])
    proc.wait()
    return proc.pid


def test_daemon_status_reports_stale_pid(workspace: Path) -> None:
    """PID file present but process dead → stale_pid=True, running=False, exit 1."""
    dead = _dead_pid()
    (workspace / ".harness" / "daemon.pid").write_text(f"{dead}\n")
    runner = CliRunner()
    result = runner.invoke(
        cli_main, ["--json", "--workspace", str(workspace), "daemon", "status"]
    )
    assert result.exit_code == 1, result.output
    data = json.loads(result.output)["data"]
    assert data["running"] is False
    assert data["stale_pid"] is True
    assert data["pid"] == dead


def test_daemon_stop_cleans_up_stale_pid(workspace: Path) -> None:
    """Stale PID file (dead process) → stop unlinks socket + PID file, exit 1."""
    dead = _dead_pid()
    pid_file = workspace / ".harness" / "daemon.pid"
    pid_file.write_text(f"{dead}\n")
    sock = resolve_socket_path(workspace)
    sock.parent.mkdir(parents=True, exist_ok=True)
    sock.write_text("")  # leftover socket file from the dead daemon
    runner = CliRunner()
    result = runner.invoke(cli_main, ["--workspace", str(workspace), "daemon", "stop"])
    assert result.exit_code == 1, result.output
    assert not pid_file.exists()
    assert not sock.exists()
