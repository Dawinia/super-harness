"""`observe` CLI subgroup — start / stop / status the optional framework-observer host.

Renamed from `daemon` (design 2026-07-03): the resident process is now purely an
observer (the gate decides in-process), so its command surface names the job it
actually does. Liveness is a pidfile-flock probe (`supervisor.is_running`); no
socket, no protocol, no ping. Vibe-coder journeys never need these commands.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import click

from super_harness.cli.errors import format_error
from super_harness.cli.output import json_envelope
from super_harness.core.paths import HarnessNotInitialized, find_harness_root
from super_harness.daemon import supervisor
from super_harness.exit_codes import EXIT_GENERIC, EXIT_NO_CONFIG, EXIT_OK


@click.group("observe")
def observe_group() -> None:
    """Operate the optional framework-observer host (start / stop / status)."""


def _resolve_root(ctx: click.Context, subcommand: str) -> Path:
    workspace = ctx.obj.get("workspace") if ctx.obj else None
    try:
        return find_harness_root(Path(workspace or "."))
    except HarnessNotInitialized as e:
        click.echo(
            format_error(subcommand=f"observe {subcommand}", message=e.message, hint=e.hint),
            err=True,
        )
        sys.exit(EXIT_NO_CONFIG)


@observe_group.command("start")
@click.pass_context
def start(ctx: click.Context) -> None:
    """Start the observer host (idempotent; blocks until live)."""
    root = _resolve_root(ctx, "start")
    # Flock-liveness wait budget. Production default 5s; under heavy CI contention
    # a host's spawn→double-fork→flock can exceed 5s, so the harness may widen it.
    # Production should never set this — 5s is the contract.
    wait_seconds = float(os.environ.get("SUPER_HARNESS_OBSERVE_START_TIMEOUT", "5.0"))
    try:
        pid = supervisor.ensure_running(root, wait_seconds=wait_seconds)
    except RuntimeError as e:
        click.echo(
            format_error(
                subcommand="observe start", message=str(e),
                hint=(
                    "check super-harness-daemon is installed alongside "
                    "super-harness and `.harness/` is writable"
                ),
            ),
            err=True,
        )
        sys.exit(EXIT_GENERIC)
    if ctx.obj.get("json"):
        click.echo(
            json_envelope(
                command="observe start", status="pass", exit_code=EXIT_OK,
                data={"pid": pid},
            )
        )
    else:
        click.echo(f"observer running (pid {pid})")
    sys.exit(EXIT_OK)


@observe_group.command("stop")
@click.pass_context
def stop(ctx: click.Context) -> None:
    """SIGTERM the observer host; wait up to 2s for it to exit."""
    root = _resolve_root(ctx, "stop")
    if not supervisor.is_running(root):
        click.echo("not running", err=True)
        sys.exit(EXIT_GENERIC)
    if supervisor.stop(root):
        click.echo("stopped")
        sys.exit(EXIT_OK)
    click.echo(
        format_error(
            subcommand="observe stop", message="observer did not shut down within 2s",
            hint="send SIGKILL to the pid in .harness/daemon.pid if it remains unresponsive",
        ),
        err=True,
    )
    sys.exit(EXIT_GENERIC)


@observe_group.command("status")
@click.pass_context
def status(ctx: click.Context) -> None:
    """Report observer host state: running / not running."""
    root = _resolve_root(ctx, "status")
    running = supervisor.is_running(root)
    pid = supervisor._read_pid(root) if running else 0
    if ctx.obj.get("json"):
        click.echo(
            json_envelope(
                command="observe status",
                status="pass" if running else "fail",
                exit_code=EXIT_OK if running else EXIT_GENERIC,
                data={"running": running, "pid": pid},
            )
        )
    elif running:
        click.echo(f"running (pid {pid})")
    else:
        click.echo("not running", err=True)
    sys.exit(EXIT_OK if running else EXIT_GENERIC)
