"""`event` subgroup — inspect the events.jsonl stream.

Per cli-command-surface §3.5. `event log` is the primary operator-facing
read path on the event store. It applies optional filtering (slug +
event-type) and limit/tail semantics, then emits either:
- human-readable TSV lines (default), or
- a JSON envelope with the filtered events (when global `--json` is set).

The JSON path serializes the *already-filtered* events list (not a fresh
unfiltered read of the file) — filtering must be honored in both modes.
"""
from __future__ import annotations

import sys
from dataclasses import asdict
from pathlib import Path

import click

from super_harness.cli.errors import format_error
from super_harness.cli.output import json_envelope
from super_harness.core.events import Event, EventSchemaError, parse_event_line
from super_harness.core.paths import (
    HarnessNotInitialized,
    events_path,
    find_harness_root,
)
from super_harness.exit_codes import EXIT_NO_CONFIG, EXIT_OK


@click.group("event")
def event_group() -> None:
    """Inspect the event stream."""


@event_group.command("log")
@click.argument("slug", required=False)
@click.option("--type", "event_type", help="Filter by event type.")
@click.option("--limit", type=int, default=50, help="Show only the last N matching events.")
@click.option(
    "--tail",
    is_flag=True,
    help="(v0.1: no-op placeholder; --limit already shows the trailing window.)",
)
@click.pass_context
def event_log(
    ctx: click.Context,
    slug: str | None,
    event_type: str | None,
    limit: int,
    tail: bool,
) -> None:
    """Print events from events.jsonl with optional filtering."""
    # `tail` is intentionally unread in v0.1; v0.2 will hook watchdog for
    # streaming follow mode. The flag exists so the CLI surface stays stable.
    _ = tail
    try:
        root = find_harness_root(Path(ctx.obj.get("workspace") or "."))
    except HarnessNotInitialized as e:
        click.echo(
            format_error(subcommand="event log", message=e.message, hint=e.hint),
            err=True,
        )
        sys.exit(EXIT_NO_CONFIG)
    events: list[Event] = []
    f = events_path(root)
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                ev = parse_event_line(line)
            except EventSchemaError:
                # Reducer-time tolerance: malformed lines are skipped, not fatal.
                # `state verify` is the strict-mode entry point for surfacing these.
                continue
            if slug and ev.change_id != slug:
                continue
            if event_type and ev.type != event_type:
                continue
            events.append(ev)
    events = events[-limit:]
    if ctx.obj.get("json"):
        click.echo(
            json_envelope(
                command="event log",
                status="pass",
                exit_code=EXIT_OK,
                data={"events": [asdict(ev) for ev in events]},
            )
        )
    else:
        for ev in events:
            click.echo(f"{ev.timestamp}\t{ev.type}\t{ev.change_id}\t{ev.actor.identifier}")
    sys.exit(EXIT_OK)
