"""`daemon` CLI subgroup — start / stop / status.

Per cli-command-surface §2.3.X (CLI surface contract) and
daemon-architecture §3.7 (behavior contract). The subgroup exists primarily
for operator debugging and CI / launchd integration; vibe-coder user
journeys do NOT require these commands — daemon auto-starts on the first
PreToolUse hook miss (per daemon-architecture §2.5).

Lifecycle path split (mirroring supervisor's two paths):
- `daemon start` → `supervisor.ensure_running(wait_for_socket=True)` —
  foreground command, blocks up to 5s until daemon is reachable.
- `daemon stop` → SIGTERM + wait up to 2s for socket file to disappear
  (AC-8). Returns 1 if not running OR shutdown timed out; stderr
  message disambiguates.
- `daemon status` → liveness probe (PID + process + ping); reports
  running / not-running / stale-pid via exit code and optional `--json`.
"""
from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path

import click

from super_harness.cli.errors import format_error
from super_harness.cli.exit_codes import EXIT_GENERIC, EXIT_NO_CONFIG, EXIT_OK
from super_harness.cli.output import json_envelope
from super_harness.core.paths import HarnessNotInitialized, find_harness_root
from super_harness.daemon import supervisor
from super_harness.daemon._uds_path import resolve_socket_path
from super_harness.daemon.protocol import PROTOCOL_VERSION
from super_harness.version import __version__


@click.group("daemon")
def daemon_group() -> None:
    """Operate the workspace daemon (start / stop / status)."""


def _resolve_root(ctx: click.Context, subcommand: str) -> Path:
    """Resolve workspace root or exit 3 with the canonical hint."""
    workspace = ctx.obj.get("workspace") if ctx.obj else None
    try:
        return find_harness_root(Path(workspace or "."))
    except HarnessNotInitialized as e:
        click.echo(
            format_error(
                subcommand=f"daemon {subcommand}", message=e.message, hint=e.hint
            ),
            err=True,
        )
        sys.exit(EXIT_NO_CONFIG)


@daemon_group.command("start")
@click.pass_context
def start(ctx: click.Context) -> None:
    """Start the workspace daemon (idempotent; blocks until ready)."""
    root = _resolve_root(ctx, "start")
    try:
        pid = supervisor.ensure_running(root, wait_seconds=5.0)
    except RuntimeError as e:
        click.echo(
            format_error(
                subcommand="daemon start",
                message=str(e),
                hint="check super-harness-daemon is on PATH and `.harness/` is writable",
            ),
            err=True,
        )
        sys.exit(EXIT_GENERIC)
    if ctx.obj.get("json"):
        click.echo(
            json_envelope(
                command="daemon start",
                status="pass",
                exit_code=EXIT_OK,
                data={"pid": pid, "socket_path": str(resolve_socket_path(root))},
            )
        )
    else:
        click.echo(f"daemon running (pid {pid})")
    sys.exit(EXIT_OK)


@daemon_group.command("stop")
@click.pass_context
def stop(ctx: click.Context) -> None:
    """Send SIGTERM; clean up socket + PID file (waits up to 2s)."""
    root = _resolve_root(ctx, "stop")
    pid_path = root / ".harness" / "daemon.pid"
    sock_path = resolve_socket_path(root)
    if not pid_path.exists():
        click.echo("not running", err=True)
        sys.exit(EXIT_GENERIC)
    try:
        pid = int(pid_path.read_text().strip())
    except (ValueError, OSError):
        click.echo("not running (PID file unreadable)", err=True)
        sys.exit(EXIT_GENERIC)
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        # Process already dead — clean up stale files.
        if sock_path.exists():
            sock_path.unlink()
        pid_path.unlink(missing_ok=True)
        click.echo("not running", err=True)
        sys.exit(EXIT_GENERIC)

    # AC-8: wait up to 2s for socket to disappear.
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if not sock_path.exists():
            break
        time.sleep(0.05)
    if sock_path.exists():
        click.echo(
            format_error(
                subcommand="daemon stop",
                message="daemon did not shut down within 2s",
                hint=f"send SIGKILL to pid {pid} if it remains unresponsive",
            ),
            err=True,
        )
        sys.exit(EXIT_GENERIC)
    # Best-effort PID file cleanup (daemon itself should unlink; clean if not).
    pid_path.unlink(missing_ok=True)
    click.echo("stopped")
    sys.exit(EXIT_OK)


@daemon_group.command("status")
@click.pass_context
def status(ctx: click.Context) -> None:
    """Report daemon state: running / stopped / stale-pid."""
    root = _resolve_root(ctx, "status")
    pid_path = root / ".harness" / "daemon.pid"
    sock_path = resolve_socket_path(root)
    use_json = bool(ctx.obj.get("json"))

    if not pid_path.exists():
        _emit_status(use_json, running=False, stale=False, pid=0,
                     socket_path=str(sock_path), uptime=0.0)
        sys.exit(EXIT_GENERIC)
    try:
        pid = int(pid_path.read_text().strip())
    except (ValueError, OSError):
        _emit_status(use_json, running=False, stale=True, pid=0,
                     socket_path=str(sock_path), uptime=0.0)
        sys.exit(EXIT_GENERIC)

    try:
        os.kill(pid, 0)
        proc_alive = True
    except ProcessLookupError:
        proc_alive = False

    if not proc_alive:
        _emit_status(use_json, running=False, stale=True, pid=pid,
                     socket_path=str(sock_path), uptime=0.0)
        sys.exit(EXIT_GENERIC)

    if not supervisor.is_running(root):
        _emit_status(use_json, running=False, stale=True, pid=pid,
                     socket_path=str(sock_path), uptime=0.0)
        sys.exit(EXIT_GENERIC)

    uptime = _read_uptime(pid_path)
    _emit_status(use_json, running=True, stale=False, pid=pid,
                 socket_path=str(sock_path), uptime=uptime)
    sys.exit(EXIT_OK)


def _read_uptime(pid_path: Path) -> float:
    """Approx uptime from PID file mtime (daemon writes the file at start)."""
    try:
        return max(0.0, time.time() - pid_path.stat().st_mtime)
    except OSError:
        return 0.0


def _emit_status(
    use_json: bool,
    *,
    running: bool,
    stale: bool,
    pid: int,
    socket_path: str,
    uptime: float,
) -> None:
    if use_json:
        click.echo(
            json_envelope(
                command="daemon status",
                status="pass" if running else "fail",
                exit_code=EXIT_OK if running else EXIT_GENERIC,
                data={
                    "running": running,
                    "stale_pid": stale,
                    "pid": pid,
                    "protocol_version": PROTOCOL_VERSION,
                    "daemon_version": __version__,
                    "uptime_seconds": uptime,
                    "socket_path": socket_path,
                },
            )
        )
        return
    if running:
        click.echo(f"running (pid {pid}, uptime {uptime:.0f}s)")
    elif stale:
        click.echo(f"stale-pid (pid {pid} not responsive)", err=True)
    else:
        click.echo("not running", err=True)
