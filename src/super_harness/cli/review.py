"""`super-harness review` — record reviewer verdicts (HG-02).

The harness does NOT run reviews (per memory project-harness-never-spawns-agent):
a human or the code agent's own reviewer subagent produces the verdict and records
it here, while the PreToolUse gate deterministically enforces that *some* verdict
exists before the lifecycle proceeds. These verbs are the record-the-verdict half.

- `review approve <change> --reviewer plan-reviewer|code-reviewer` → emits
  `plan_approved` / `code_review_passed`.
- `review reject  <change> --reviewer ...` → emits `plan_rejected` / `code_review_failed`.
- `review skip    <change> --reviewer ...` → escape hatch (== approve, reason=manual_skip;
  cli-command-surface §499, sensor-gate §3.6 #7).

Emit is STRICT — an illegal transition (wrong current state) is rejected, nothing
appended. Exit codes: 0 ok / 2 bad reviewer (click) or illegal transition / 3 no
`.harness/`. (Reconcile note: cli-command-surface §509 lists `review skip` as 0/1/3/5;
that omits the EmitPreconditionError path — house convention is EXIT_VALIDATION=2.)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from super_harness.cli.errors import format_error
from super_harness.cli.output import json_envelope
from super_harness.core.clock import utc_now_iso
from super_harness.core.emit_validation import EmitPreconditionError
from super_harness.core.events import Actor, Event
from super_harness.core.identity import resolve_identity
from super_harness.core.paths import (
    HarnessNotInitialized,
    events_path,
    find_harness_root,
    pending_reviews_dir,
)
from super_harness.core.post_emit import refresh_state_after_emit
from super_harness.core.reducer import derive_state
from super_harness.core.review_bundle import BundleError, assemble_bundle
from super_harness.core.ulid import new_event_id
from super_harness.core.writer import EventWriter
from super_harness.exit_codes import EXIT_NO_CONFIG, EXIT_OK, EXIT_VALIDATION

# Reviewer name → the (pass, fail) extension events its verdict emits.
_REVIEWER_PASS: dict[str, str] = {
    "plan-reviewer": "plan_approved",
    "code-reviewer": "code_review_passed",
}
_REVIEWER_FAIL: dict[str, str] = {
    "plan-reviewer": "plan_rejected",
    "code-reviewer": "code_review_failed",
}
_REVIEWERS = sorted(_REVIEWER_PASS)
_reviewer_opt = click.option(
    "--reviewer", required=True, type=click.Choice(_REVIEWERS),
    help="plan-reviewer or code-reviewer.",
)


@click.group("review")
def review_group() -> None:
    """Record reviewer verdicts (approve / reject) or skip a stuck reviewer."""


def _emit_verdict(
    ctx: click.Context, *, subcommand: str, change: str, reviewer: str, event_type: str,
    reason: str, as_identity: str | None = None, extra_payload: dict[str, object] | None = None,
) -> None:
    """Shared body for skip/approve/reject: emit the verdict event (STRICT) + report.

    The harness does NOT run the review — a human or the code agent's own reviewer
    subagent produces the verdict and records it here; the gate deterministically
    enforces that *some* verdict exists before the lifecycle proceeds (see memory
    project-harness-never-spawns-agent).
    """
    try:
        root = find_harness_root(Path(ctx.obj.get("workspace") or "."))
    except HarnessNotInitialized as e:
        click.echo(format_error(subcommand=subcommand, message=e.message, hint=e.hint), err=True)
        sys.exit(EXIT_NO_CONFIG)

    cs = derive_state(events_path(root)).get(change)
    framework = cs.framework if cs is not None else "plain"  # like HG-01
    ev = Event(
        event_id=new_event_id(),
        type=event_type,
        change_id=change,
        timestamp=utc_now_iso(),
        actor=Actor(type="human", identifier=resolve_identity(root, as_identity)),
        framework=framework,
        payload={"reviewer": reviewer, "reason": reason, **(extra_payload or {})},
    )
    try:
        EventWriter(events_path(root)).emit(ev)
    except EmitPreconditionError as e:
        click.echo(
            format_error(
                subcommand=subcommand,
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
                command=subcommand,
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


_as_opt = click.option(
    "--as", "as_identity", default=None,
    help="Reviewer identity recorded on the event "
    "(default: env SUPER_HARNESS_ACTOR, else `git config user.email`, else `cli`).",
)


@review_group.command("approve")
@click.argument("change")
@_reviewer_opt
@click.option("--reason", default="approved", help="Audit reason recorded on the event.")
@_as_opt
@click.pass_context
def approve(ctx: click.Context, change: str, reviewer: str, reason: str,
            as_identity: str | None) -> None:
    """Record a PASS verdict: emit `plan_approved` / `code_review_passed`."""
    _emit_verdict(
        ctx, subcommand="review approve", change=change, reviewer=reviewer,
        event_type=_REVIEWER_PASS[reviewer], reason=reason, as_identity=as_identity,
    )


@review_group.command("reject")
@click.argument("change")
@_reviewer_opt
@click.option("--reason", default="rejected", help="Audit reason recorded on the event.")
@_as_opt
@click.pass_context
def reject(ctx: click.Context, change: str, reviewer: str, reason: str,
           as_identity: str | None) -> None:
    """Record a FAIL verdict: emit `plan_rejected` / `code_review_failed`."""
    _emit_verdict(
        ctx, subcommand="review reject", change=change, reviewer=reviewer,
        event_type=_REVIEWER_FAIL[reviewer], reason=reason, as_identity=as_identity,
    )


@review_group.command("skip")
@click.argument("change")
@_reviewer_opt
@click.option("--reason", default="manual_skip", help="Audit reason recorded on the event.")
@_as_opt
@click.pass_context
def skip(ctx: click.Context, change: str, reviewer: str, reason: str,
         as_identity: str | None) -> None:
    """Escape hatch — PASS a stuck reviewer (== approve with reason=manual_skip).

    Stamps a structured ``payload["skipped"] = True`` marker so the merge-boundary
    independence disclosure can distinguish a *skipped* review from a real one,
    independent of the free-text ``--reason`` (HG-12 cut 1).
    """
    _emit_verdict(
        ctx, subcommand="review skip", change=change, reviewer=reviewer,
        event_type=_REVIEWER_PASS[reviewer], reason=reason, as_identity=as_identity,
        extra_payload={"skipped": True},
    )


@review_group.command("prepare")
@click.argument("change")
@_reviewer_opt
@click.option("--base", default=None, help="Base branch for the in-scope diff "
              "(default: .harness/policy.yaml review.base_branch, else main).")
@click.pass_context
def prepare(ctx: click.Context, change: str, reviewer: str, base: str | None) -> None:
    """Assemble the review bundle (diff∩scope + checklist + digest) → disk.

    The harness does NOT review — this hands the reviewer subagent a complete,
    deterministic context to review against. Requires a clean in-scope tree.
    """
    try:
        root = find_harness_root(Path(ctx.obj.get("workspace") or "."))
    except HarnessNotInitialized as e:
        click.echo(format_error(subcommand="review prepare", message=e.message, hint=e.hint),
                   err=True)
        sys.exit(EXIT_NO_CONFIG)
    try:
        bundle = assemble_bundle(root, change_id=change, reviewer=reviewer, base=base)
    except BundleError as e:
        click.echo(format_error(subcommand="review prepare", message=str(e),
                                hint="Commit the in-scope changes, then re-run review prepare."),
                   err=True)
        sys.exit(EXIT_VALIDATION)
    out_dir = pending_reviews_dir(root, change)
    out_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = out_dir / f"{reviewer}.bundle.json"
    bundle_path.write_text(json.dumps(bundle, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if ctx.obj.get("json"):
        click.echo(json_envelope(command="review prepare", status="pass", exit_code=EXIT_OK,
                                 data={"change": change, "reviewer": reviewer,
                                       "bundle_path": str(bundle_path),
                                       "bundle_digest": bundle["bundle_digest"],
                                       "diff_in_scope": bundle["diff_in_scope"],
                                       "out_of_scope": bundle["out_of_scope"]}))
    elif not ctx.obj.get("quiet"):
        click.echo(f"super-harness: wrote review bundle for {change} ({reviewer}) → {bundle_path}")
        if bundle["out_of_scope"]:
            click.echo("  out-of-scope changes (review carefully):\n    "
                       + "\n    ".join(bundle["out_of_scope"]))
    sys.exit(EXIT_OK)
