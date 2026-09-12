"""`super-harness implementation` — implementation-phase lifecycle emitters.

`implementation start <slug> [--first-commit <sha>]` (cli-command-surface §429)
manually emits `implementation_started`, advancing PLAN_APPROVED →
IMPLEMENTATION_IN_PROGRESS. This is the third v0.1 lifecycle-gap emitter; with
`review skip` it lets a cold-start change traverse the whole lifecycle via CLI
(no `skip_validation` seeding). v1 is a manual verb; auto-detecting the first
scope-file edit (per lifecycle-event-model §3.3) is deferred (needs activity
events / git-hook infra, tracked under HG-11). Emit is STRICT.

`implementation reopen <slug> --reason <why>` emits `implementation_invalidated`,
returning a frozen change (AWAITING_CODE_REVIEW / READY_TO_MERGE) to
IMPLEMENTATION_IN_PROGRESS so a review finding can be folded into the change that
raised it. Before it existed the only CLI route back was `plan redeclare` into a whole
plan cycle. See docs/plans/2026-08-11-code-only-recovery.md.

Exit codes: 0 ok / 2 illegal transition / 3 no `.harness/` (per spec §435, 0/1/2/3/5).
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from super_harness.cli.errors import format_error
from super_harness.cli.output import json_envelope
from super_harness.core.approval import ApprovalError, load_json_record
from super_harness.core.clock import utc_now_iso
from super_harness.core.emit_validation import EmitPreconditionError
from super_harness.core.events import Actor, Event
from super_harness.core.identity import resolve_identity
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
    if cs is None or (cs.effective_approval is None and cs.legacy_plan_approval is None):
        click.echo(
            format_error(
                subcommand="implementation start",
                message="implementation requires an applicable plan approval",
                hint="Import a recognized plan approval before starting implementation.",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
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
        click.echo(f"super-harness: emitted implementation_started for {slug} → {new_state}")
    sys.exit(EXIT_OK)


# The only states `reopen` accepts. `implementation_invalidated` is legal from any active
# state (`core/transitions.py`), so this narrowing is the CLI's, and it is the whole
# safety argument for the verb.
#
# These two mean "the code is written, and under or past code review" — the entire
# population of the case the verb exists for. Every other state either already permits
# edits (PLAN_APPROVED / IMPLEMENTATION_IN_PROGRESS / CODE_REVIEW_REJECTED), has nothing
# to reopen (INTENT_DECLARED / AWAITING_PLAN_REVIEW), or is terminal.
#
# PLAN_REJECTED is excluded ON PURPOSE. Reopening out of a rejection discards it: the
# change returns to IMPLEMENTATION_IN_PROGRESS, `done` and code review carry it to
# READY_TO_MERGE, and the merge gate is satisfied by the stale `plan_approved` from the
# earlier epoch. Nothing here can tell "the reviewer's findings were code-level" — the
# case that motivated this verb — from "the reviewer rejected the plan", so admitting the
# state would make plan rejection advisory for any change that has implemented once. The
# exit from a rejection is `review skip --override --reason`, which emits the same
# `plan_approved` but stamps `skipped: True` and is refused at the merge gate unless the
# override is deliberate. Escaping a rejection costs a disclosure; reopening a passed
# review does not.
#
# No milestone precondition accompanies this: both states already imply `plan_approved`
# and `implementation_complete` through the transition table (AWAITING_CODE_REVIEW is
# reachable only by `implementation_complete` from IMPLEMENTATION_IN_PROGRESS, itself
# reachable only from PLAN_APPROVED). A guard restating what the state already proves is
# the added structure this repository keeps paying for.
_REOPEN_STATES: tuple[str, ...] = ("AWAITING_CODE_REVIEW", "READY_TO_MERGE")


@implementation_group.command("reopen")
@click.argument("slug")
@click.option(
    "--reason",
    required=True,
    help="Why the frozen implementation is being reopened (recorded on the event and "
    "counted by `super-harness report`).",
)
@click.pass_context
def reopen(ctx: click.Context, slug: str, reason: str) -> None:
    """Emit `implementation_invalidated` — reopen a frozen change for a code-only fix.

    `AWAITING_CODE_REVIEW` / `READY_TO_MERGE` → `IMPLEMENTATION_IN_PROGRESS`, so a
    finding can be folded into the change that produced it without the `plan redeclare`
    round trip through plan review. `--reason` is required: this voids the code review
    the change was under or had passed, which is the consequence class of
    `review authorize`.
    """
    try:
        root = find_harness_root(Path(ctx.obj.get("workspace") or "."))
    except HarnessNotInitialized as e:
        click.echo(
            format_error(subcommand="implementation reopen", message=e.message, hint=e.hint),
            err=True,
        )
        sys.exit(EXIT_NO_CONFIG)

    cs = derive_state(events_path(root)).get(slug)
    current = cs.current_state if cs is not None else None
    # Checked here rather than left to the emit-time transition table, which would accept
    # every active state: this narrowing IS the policy, so it must fail before anything
    # is appended.
    if current not in _REOPEN_STATES:
        click.echo(
            format_error(
                subcommand="implementation reopen",
                message=(
                    f"change {slug!r} is {current or 'unknown (no such change)'}, "
                    f"not one of {', '.join(_REOPEN_STATES)}"
                ),
                hint=(
                    "`reopen` returns a frozen implementation to editing. From "
                    "PLAN_REJECTED, revise and re-submit the plan; if the reviewer is "
                    'genuinely stuck, `review skip --override --reason "<why>"` is the '
                    "disclosed escape hatch."
                ),
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    if cs is None or (cs.effective_approval is None and cs.legacy_plan_approval is None):
        click.echo(
            format_error(
                subcommand="implementation reopen",
                message="cannot reopen without an applicable plan approval",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)

    ev = Event(
        event_id=new_event_id(),
        type="implementation_invalidated",
        change_id=slug,
        timestamp=utc_now_iso(),
        # `resolve_identity`, not the `cli` placeholder `implementation start` uses:
        # `report` renders this actor in the reopen row, and `cli` is exactly what
        # `attestation.PLACEHOLDER_IDENTITY` renders as "unattributed" elsewhere. The
        # surface this verb mirrors records why (AUTH-003) — with two people on a repo,
        # unnamed rows leave neither able to falsify the ones that are not theirs.
        actor=Actor(type="human", identifier=resolve_identity(root, None)),
        framework=cs.framework if cs is not None else "plain",
        payload={"reason": reason},
    )
    try:
        EventWriter(events_path(root)).emit(ev)
    except EmitPreconditionError as e:
        click.echo(
            format_error(
                subcommand="implementation reopen",
                message=str(e),
                hint="`implementation_invalidated` is not legal from this state.",
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
                command="implementation reopen",
                status="pass",
                exit_code=EXIT_OK,
                data={
                    "change": slug,
                    "event_emitted": "implementation_invalidated",
                    "reason": reason,
                    "new_state": new_state,
                },
            )
        )
    elif not ctx.obj.get("quiet"):
        click.echo(f"super-harness: emitted implementation_invalidated for {slug} → {new_state}")
        # The cost, stated where the verb is used rather than folded into its name. An
        # agent that reads `reopen` as free needs to be told here what it just spent.
        #
        # "whatever ... had" rather than "already passed": this verb also accepts
        # AWAITING_CODE_REVIEW, where the round is still out and nothing has passed.
        # Naming a passed review there would be false in half the cases the verb exists
        # for, and the gate's own suggestion for that state already says "the round".
        click.echo(
            "  whatever code review this change had no longer stands; "
            "run `done` and review again before merge"
        )
    sys.exit(EXIT_OK)


@implementation_group.command("record")
@click.argument("slug")
@click.option("--assessment", required=True, type=click.Path(exists=True, dir_okay=False))
@click.pass_context
def record(ctx: click.Context, slug: str, assessment: str) -> None:
    """Record implementation reasoning and the actual coverage manifest."""
    subcommand = "implementation record"
    try:
        root = find_harness_root(Path(ctx.obj.get("workspace") or "."))
    except HarnessNotInitialized as exc:
        click.echo(
            format_error(subcommand=subcommand, message=exc.message, hint=exc.hint),
            err=True,
        )
        sys.exit(EXIT_NO_CONFIG)
    cs = derive_state(events_path(root)).get(slug)
    if cs is None or (cs.effective_approval is None and cs.legacy_plan_approval is None):
        click.echo(
            format_error(subcommand=subcommand, message="no applicable plan approval"),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    try:
        value = load_json_record(Path(assessment))
    except ApprovalError as exc:
        click.echo(format_error(subcommand=subcommand, message=str(exc)), err=True)
        sys.exit(EXIT_VALIDATION)
    assessment_value = value.get("assessment", value)
    coverage = value.get("coverage", value.get("coverage_manifest", []))
    if not isinstance(assessment_value, dict) or not isinstance(coverage, list):
        click.echo(
            format_error(
                subcommand=subcommand,
                message="assessment must contain an object and a coverage list",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    approval_id = (
        cs.effective_approval.get("approval_id")
        if isinstance(cs.effective_approval, dict)
        else "legacy"
    )
    requested = assessment_value.get("approval_id", value.get("approval_id", approval_id))
    if requested != approval_id:
        click.echo(
            format_error(
                subcommand=subcommand,
                message="assessment does not reference the effective approval",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    if not all(isinstance(item, dict) for item in coverage):
        click.echo(
            format_error(subcommand=subcommand, message="coverage entries must be objects"),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    payload = {
        "assessment": {
            **assessment_value,
            "approval_id": approval_id,
            "change_id": slug,
        },
        "coverage": [dict(item) for item in coverage],
    }
    ev = Event(
        event_id=new_event_id(),
        type="implementation_recorded",
        change_id=slug,
        timestamp=utc_now_iso(),
        actor=Actor(type="human", identifier=resolve_identity(root, None)),
        framework=cs.framework,
        payload=payload,
    )
    try:
        EventWriter(events_path(root)).emit(ev)
    except EmitPreconditionError as exc:
        click.echo(format_error(subcommand=subcommand, message=str(exc)), err=True)
        sys.exit(EXIT_VALIDATION)
    refresh_state_after_emit(root)
    if ctx.obj.get("json"):
        click.echo(
            json_envelope(
                command=subcommand,
                status="pass",
                exit_code=EXIT_OK,
                data={"change": slug, "coverage": len(coverage)},
            )
        )
    elif not ctx.obj.get("quiet"):
        click.echo(f"super-harness: recorded implementation assessment for {slug}")
    sys.exit(EXIT_OK)
