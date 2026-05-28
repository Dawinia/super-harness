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
