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
from super_harness.core.review_bundle import BundleError, assemble_bundle, load_base_branch
from super_harness.core.review_checklist import resolve_checklist
from super_harness.core.review_verdict import (
    VerdictError,
    check_coverage,
    check_disposed,
    derive_open_findings,
    parse_verdict_file,
    read_change_events,
)
from super_harness.core.scope_match import (
    GitScopeError,
    committed_scope_digest,
    split_changed_by_scope,
    working_tree_dirty,
)
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


def _validate_code_review_verdict(
    root: Path, change: str, reviewer: str, verdict_file: str | None, base: str | None,
    subcommand: str,
) -> dict[str, object]:
    """Validate the structured verdict for a code-review approval (emit-time teeth).

    Returns the parsed verdict dict to inline into the event payload, or exits
    (EXIT_VALIDATION) with a structured error. Fail-closed on git errors.
    """
    if not verdict_file:
        click.echo(format_error(subcommand=subcommand,
            message="code-reviewer approval requires a structured verdict.",
            hint="Run `review prepare`, review the bundle, then pass --verdict-file <path>."),
            err=True)
        sys.exit(EXIT_VALIDATION)
    try:
        verdict = parse_verdict_file(Path(verdict_file))
    except VerdictError as e:
        click.echo(format_error(subcommand=subcommand, message=str(e)), err=True)
        sys.exit(EXIT_VALIDATION)

    required = resolve_checklist(root, reviewer)
    missing = check_coverage(verdict, required)
    if missing:
        click.echo(format_error(subcommand=subcommand,
            message=f"verdict does not cover every checklist item; missing: {', '.join(missing)}",
            hint="Every checklist item must have a status (pass/fail/na)."), err=True)
        sys.exit(EXIT_VALIDATION)

    resolved_base = base or load_base_branch(root)
    cs = derive_state(events_path(root)).get(change)
    declared = list(cs.scope.get("files", [])) if cs is not None else []
    if working_tree_dirty(root, declared):
        click.echo(format_error(subcommand=subcommand,
            message="in-scope files have uncommitted changes; cannot verify the reviewed diff.",
            hint="Commit the in-scope changes and re-run review prepare + approve."), err=True)
        sys.exit(EXIT_VALIDATION)
    try:
        in_scope, _ = split_changed_by_scope(root, base=resolved_base, declared=declared)
        current = committed_scope_digest(root, base=resolved_base, in_scope=in_scope)
    except GitScopeError as e:
        click.echo(format_error(subcommand=subcommand,
            message=f"cannot verify review freshness (git error): {e}",
            hint="Resolve the git/base-branch issue; the gate fails closed."), err=True)
        sys.exit(EXIT_VALIDATION)
    if verdict["bundle_digest"] != current:
        click.echo(format_error(subcommand=subcommand,
            message="verdict is stale — its bundle_digest does not match the in-scope diff.",
            hint="The code changed since `review prepare`; re-prepare and re-review."), err=True)
        sys.exit(EXIT_VALIDATION)

    # D (slice-2): an approve emitted FROM CODE_REVIEW_REJECTED must dispose every
    # open finding from prior code_review_failed verdicts. Inert otherwise.
    if cs is not None and cs.current_state == "CODE_REVIEW_REJECTED":
        events = read_change_events(events_path(root), change)
        open_ids = derive_open_findings(events, change)
        undisposed = check_disposed(verdict, open_ids)
        if undisposed:
            click.echo(format_error(subcommand=subcommand,
                message=f"approve does not dispose prior open finding(s): {', '.join(undisposed)}",
                hint="Add a prior_findings entry (resolved | wontfix+note) for each open finding."),
                err=True)
            sys.exit(EXIT_VALIDATION)
    return verdict


@review_group.command("approve")
@click.argument("change")
@_reviewer_opt
@click.option("--reason", default="approved", help="Audit reason recorded on the event.")
@click.option("--verdict-file", default=None, help="Structured verdict file "
              "(REQUIRED for code-reviewer; see `review prepare`).")
@click.option("--base", default=None, help="Base branch for freshness check "
              "(default: policy.yaml review.base_branch, else main).")
@_as_opt
@click.pass_context
def approve(ctx: click.Context, change: str, reviewer: str, reason: str,
            verdict_file: str | None, base: str | None, as_identity: str | None) -> None:
    """Record a PASS verdict: emit `plan_approved` / `code_review_passed`."""
    extra: dict[str, object] | None = None
    if reviewer == "code-reviewer":
        try:
            root = find_harness_root(Path(ctx.obj.get("workspace") or "."))
        except HarnessNotInitialized as e:
            click.echo(format_error(subcommand="review approve", message=e.message, hint=e.hint),
                       err=True)
            sys.exit(EXIT_NO_CONFIG)
        verdict = _validate_code_review_verdict(
            root, change, reviewer, verdict_file, base, "review approve")
        extra = {"verdict": verdict}
    elif verdict_file:  # plan-reviewer: inline if provided, not required (advisory this slice)
        try:
            extra = {"verdict": parse_verdict_file(Path(verdict_file))}
        except VerdictError as e:
            click.echo(format_error(subcommand="review approve", message=str(e)), err=True)
            sys.exit(EXIT_VALIDATION)
    _emit_verdict(
        ctx, subcommand="review approve", change=change, reviewer=reviewer,
        event_type=_REVIEWER_PASS[reviewer], reason=reason, as_identity=as_identity,
        extra_payload=extra,
    )


@review_group.command("reject")
@click.argument("change")
@_reviewer_opt
@click.option("--reason", default="rejected", help="Audit reason recorded on the event.")
@click.option("--verdict-file", default=None, help="Structured verdict file "
              "(inlined if provided; never required for reject).")
@_as_opt
@click.pass_context
def reject(ctx: click.Context, change: str, reviewer: str, reason: str,
           verdict_file: str | None, as_identity: str | None) -> None:
    """Record a FAIL verdict: emit `plan_rejected` / `code_review_failed`."""
    extra: dict[str, object] | None = None
    if verdict_file:
        try:
            extra = {"verdict": parse_verdict_file(Path(verdict_file))}
        except VerdictError as e:
            click.echo(format_error(subcommand="review reject", message=str(e)), err=True)
            sys.exit(EXIT_VALIDATION)
    _emit_verdict(
        ctx, subcommand="review reject", change=change, reviewer=reviewer,
        event_type=_REVIEWER_FAIL[reviewer], reason=reason, as_identity=as_identity,
        extra_payload=extra,
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
