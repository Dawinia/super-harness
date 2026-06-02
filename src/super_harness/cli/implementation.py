"""`super-harness implementation` — implementation_started emitter (HG-02.3).

`implementation start <slug> [--first-commit <sha>]` (cli-command-surface §429)
manually emits `implementation_started`, advancing PLAN_APPROVED →
IMPLEMENTATION_IN_PROGRESS. This is the third v0.1 lifecycle-gap emitter; with
`review skip` it lets a cold-start change traverse the whole lifecycle via CLI
(no `skip_validation` seeding). v1 is a manual verb; auto-detecting the first
scope-file edit (per lifecycle-event-model §3.3) is deferred (needs activity
events / git-hook infra, tracked under HG-11). Emit is STRICT.

Exit codes: 0 ok / 2 illegal transition / 3 no `.harness/` (per spec §435, 0/1/2/3/5).
"""
from __future__ import annotations

import sys
from pathlib import Path

import click

from super_harness.cli.errors import format_error
from super_harness.cli.output import json_envelope
from super_harness.core.clock import utc_now_iso
from super_harness.core.emit_validation import EmitPreconditionError
from super_harness.core.events import Actor, Event
from super_harness.core.paths import (
    HarnessNotInitialized,
    events_path,
    find_harness_root,
)
from super_harness.core.post_emit import refresh_state_after_emit
from super_harness.core.reducer import derive_state
from super_harness.core.ulid import new_event_id
from super_harness.core.writer import EventWriter
from super_harness.exit_codes import EXIT_NO_CONFIG, EXIT_OK, EXIT_VALIDATION


@click.group("implementation")
def implementation_group() -> None:
    """Implementation-phase lifecycle verbs."""


@implementation_group.command("start")
@click.argument("slug")
@click.option(
    "--first-commit",
    default=None,
    help="The commit sha that began implementation (recorded on the event payload).",
)
@click.pass_context
def start(ctx: click.Context, slug: str, first_commit: str | None) -> None:
    """Emit `implementation_started` (PLAN_APPROVED → IMPLEMENTATION_IN_PROGRESS)."""
    try:
        root = find_harness_root(Path(ctx.obj.get("workspace") or "."))
    except HarnessNotInitialized as e:
        click.echo(
            format_error(subcommand="implementation start", message=e.message, hint=e.hint),
            err=True,
        )
        sys.exit(EXIT_NO_CONFIG)

    cs = derive_state(events_path(root)).get(slug)
    framework = cs.framework if cs is not None else "plain"
    payload: dict[str, str] = {}
    if first_commit:
        payload["first_commit"] = first_commit
    ev = Event(
        event_id=new_event_id(),
        type="implementation_started",
        change_id=slug,
        timestamp=utc_now_iso(),
        actor=Actor(type="human", identifier="cli"),
        framework=framework,
        payload=payload,
    )
    try:
        EventWriter(events_path(root)).emit(ev)
    except EmitPreconditionError as e:
        click.echo(
            format_error(
                subcommand="implementation start",
                message=str(e),
                hint="`implementation_started` is only legal from PLAN_APPROVED.",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    refresh_state_after_emit(root)

    new_cs = derive_state(events_path(root)).get(slug)
    new_state = new_cs.current_state if new_cs is not None else None
    if ctx.obj.get("json"):
        click.echo(
            json_envelope(
                command="implementation start",
                status="pass",
                exit_code=EXIT_OK,
                data={
                    "change": slug,
                    "event_emitted": "implementation_started",
                    "first_commit": first_commit,
                    "new_state": new_state,
                },
            )
        )
    elif not ctx.obj.get("quiet"):
        click.echo(
            f"super-harness: emitted implementation_started for {slug} → {new_state}"
        )
    sys.exit(EXIT_OK)
