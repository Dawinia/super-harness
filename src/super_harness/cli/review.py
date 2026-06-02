"""`super-harness review` — reviewer escape-hatch CLI (HG-02, first increment).

`review skip <change> --reviewer plan-reviewer|code-reviewer` explicitly emits
`plan_approved` / `code_review_passed` (cli-command-surface §499, sensor-gate
§3.6 #7). This closes 2 of the 3 v0.1 lifecycle-gap emitters by letting a human
advance the lifecycle past a reviewer. Emit is STRICT — an illegal transition
(wrong current state) is rejected and nothing is appended.

Exit codes: 0 ok / 2 bad reviewer (click) or illegal transition / 3 no `.harness/`.
(Reconcile note: cli-command-surface §509 lists 0/1/3/5; that enumeration omits
the EmitPreconditionError path. House convention across `change start`/`abandon`/
`done` is EXIT_VALIDATION=2 for an illegal lifecycle transition.)
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

# Reviewer name → the extension event a skip emits.
_REVIEWER_EVENT: dict[str, str] = {
    "plan-reviewer": "plan_approved",
    "code-reviewer": "code_review_passed",
}


@click.group("review")
def review_group() -> None:
    """Reviewer escape hatches (advance the lifecycle past a stuck reviewer)."""


@review_group.command("skip")
@click.argument("change")
@click.option(
    "--reviewer",
    required=True,
    type=click.Choice(sorted(_REVIEWER_EVENT)),
    help="Which reviewer to skip (plan-reviewer → plan_approved, "
    "code-reviewer → code_review_passed).",
)
@click.option("--reason", default="manual_skip", help="Audit reason recorded on the event.")
@click.pass_context
def skip(ctx: click.Context, change: str, reviewer: str, reason: str) -> None:
    """Emit `plan_approved` / `code_review_passed` to advance past a reviewer."""
    try:
        root = find_harness_root(Path(ctx.obj.get("workspace") or "."))
    except HarnessNotInitialized as e:
        click.echo(
            format_error(subcommand="review skip", message=e.message, hint=e.hint),
            err=True,
        )
        sys.exit(EXIT_NO_CONFIG)

    event_type = _REVIEWER_EVENT[reviewer]
    # Record the change's framework (set by intent_declared) on the event, like HG-01.
    cs = derive_state(events_path(root)).get(change)
    framework = cs.framework if cs is not None else "plain"
    ev = Event(
        event_id=new_event_id(),
        type=event_type,
        change_id=change,
        timestamp=utc_now_iso(),
        actor=Actor(type="human", identifier="cli"),
        framework=framework,
        payload={"reviewer": reviewer, "reason": reason},
    )
    try:
        EventWriter(events_path(root)).emit(ev)
    except EmitPreconditionError as e:
        click.echo(
            format_error(
                subcommand="review skip",
                message=str(e),
                hint=f"`{event_type}` is not legal from the change's current state.",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    refresh_state_after_emit(root)

    new_cs = derive_state(events_path(root)).get(change)
    new_state = new_cs.current_state if new_cs is not None else None
    if ctx.obj.get("json"):
        click.echo(
            json_envelope(
                command="review skip",
                status="pass",
                exit_code=EXIT_OK,
                data={
                    "change": change,
                    "reviewer": reviewer,
                    "event_emitted": event_type,
                    "new_state": new_state,
                },
            )
        )
    elif not ctx.obj.get("quiet"):
        click.echo(
            f"super-harness: emitted {event_type} for {change} "
            f"(reviewer={reviewer}, reason={reason}) → {new_state}"
        )
    sys.exit(EXIT_OK)
