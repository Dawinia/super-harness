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

from super_harness.adapters.registry import resolve_spec_plan_paths
from super_harness.cli.errors import format_error
from super_harness.cli.output import json_envelope
from super_harness.core.clock import utc_now_iso
from super_harness.core.doc_refs import scan_doc_refs
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
    failing_items,
    parse_verdict_file,
    read_change_events,
)
from super_harness.core.scope_match import (
    GitScopeError,
    committed_scope_digest,
    resolve_commit,
    split_changed_by_scope,
    working_tree_dirty,
)
from super_harness.core.ulid import new_event_id
from super_harness.core.writer import EventWriter
from super_harness.engineering.review_contract import ReviewContractError, compile_review_contract
from super_harness.engineering.reviewer_policy import (
    ReviewerIndependencePolicy,
    ReviewerPolicyError,
    approved_review_sources,
    load_reviewer_policy,
    reviewer_policy_payload,
)
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
_source_opt = click.option(
    "--source",
    default=None,
    help="Reviewer source label from policy.yaml reviewers.sources.",
)

_REVIEWER_STATES: dict[str, set[str]] = {
    "plan-reviewer": {"AWAITING_PLAN_REVIEW"},
    "code-reviewer": {"AWAITING_CODE_REVIEW", "CODE_REVIEW_REJECTED"},
}

def _load_policy_or_exit(root: Path, reviewer: str, subcommand: str) -> ReviewerIndependencePolicy:
    try:
        return load_reviewer_policy(root, reviewer)
    except ReviewerPolicyError as e:
        click.echo(format_error(subcommand=subcommand, message=str(e)), err=True)
        sys.exit(EXIT_VALIDATION)


def _validate_source_or_exit(
    *,
    policy: ReviewerIndependencePolicy,
    source: str | None,
    subcommand: str,
    require_for_threshold: bool = True,
) -> None:
    if require_for_threshold and policy.min_independent >= 2 and not source:
        click.echo(
            format_error(
                subcommand=subcommand,
                message=(
                    f"{policy.reviewer} requires {policy.min_independent} independent "
                    "reviewer source(s); --source is required."
                ),
                hint="Pass --source <name> using a name from reviewers.sources in policy.yaml.",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    if source and policy.allowed_sources and source not in policy.allowed_sources:
        click.echo(
            format_error(
                subcommand=subcommand,
                message=f"unknown reviewer source for {policy.reviewer}: {source!r}",
                hint=f"Configured sources: {', '.join(policy.allowed_sources)}",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    if source and policy.participants and source not in policy.participants:
        click.echo(
            format_error(
                subcommand=subcommand,
                message=f"source {source!r} is not a participant for {policy.reviewer}",
                hint=f"Configured participants: {', '.join(policy.participants)}",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)


def _validate_reviewer_state_or_exit(
    cs: object | None, *, reviewer: str, subcommand: str,
) -> None:
    current = getattr(cs, "current_state", None)
    allowed = _REVIEWER_STATES[reviewer]
    if current not in allowed:
        click.echo(
            format_error(
                subcommand=subcommand,
                message=f"{reviewer} cannot record a verdict from state {current!r}",
                hint=f"Expected state: {', '.join(sorted(allowed))}.",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)


def _change_events(root: Path, change: str) -> list[Event]:
    return read_change_events(events_path(root), change)


def _prepared_target_head(root: Path, change: str, reviewer: str) -> str | None:
    bundle_path = pending_reviews_dir(root, change) / f"{reviewer}.bundle.json"
    try:
        parsed = json.loads(bundle_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    target = parsed.get("target_head") if isinstance(parsed, dict) else None
    return target if isinstance(target, str) and target else None


def _validate_prepared_target_head(
    root: Path, change: str, reviewer: str, subcommand: str
) -> str | None:
    prepared = _prepared_target_head(root, change, reviewer)
    if prepared is None:
        return None
    try:
        current = resolve_commit(root)
    except GitScopeError as e:
        click.echo(
            format_error(
                subcommand=subcommand,
                message=f"cannot verify prepared target HEAD: {e}",
                hint="Resolve the Git history error and re-run review prepare.",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    if prepared != current:
        click.echo(
            format_error(
                subcommand=subcommand,
                message="prepared review target HEAD is stale",
                hint="HEAD changed after review prepare; re-prepare and re-review.",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    return prepared


def _emit_review_event(
    root: Path,
    *,
    change: str,
    reviewer: str,
    event_type: str,
    reason: str,
    actor: Actor,
    framework: str,
    payload: dict[str, object],
    subcommand: str,
) -> None:
    ev = Event(
        event_id=new_event_id(),
        type=event_type,
        change_id=change,
        timestamp=utc_now_iso(),
        actor=actor,
        framework=framework,  # type: ignore[arg-type]
        payload={"reviewer": reviewer, "reason": reason, **payload},
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


def _emit_cumulative_approve(
    ctx: click.Context,
    *,
    change: str,
    reviewer: str,
    reason: str,
    as_identity: str | None,
    source: str | None,
    extra_payload: dict[str, object] | None,
) -> None:
    subcommand = "review approve"
    try:
        root = find_harness_root(Path(ctx.obj.get("workspace") or "."))
    except HarnessNotInitialized as e:
        click.echo(format_error(subcommand=subcommand, message=e.message, hint=e.hint), err=True)
        sys.exit(EXIT_NO_CONFIG)

    policy = _load_policy_or_exit(root, reviewer, subcommand)
    _validate_source_or_exit(policy=policy, source=source, subcommand=subcommand)
    cs = derive_state(events_path(root)).get(change)
    _validate_reviewer_state_or_exit(cs, reviewer=reviewer, subcommand=subcommand)

    # Backwards-compatible path: no source declaration means no partial source event.
    if policy.min_independent == 1 and source is None:
        _emit_verdict(
            ctx, subcommand=subcommand, change=change, reviewer=reviewer,
            event_type=_REVIEWER_PASS[reviewer], reason=reason, as_identity=as_identity,
            extra_payload=extra_payload,
        )

    framework = cs.framework if cs is not None else "plain"
    actor = Actor(type="human", identifier=resolve_identity(root, as_identity))
    partial_payload: dict[str, object] = {
        "source": source,
        "outcome": "approved",
        **(extra_payload or {}),
    }
    _emit_review_event(
        root,
        change=change,
        reviewer=reviewer,
        event_type="review_verdict_recorded",
        reason=reason,
        actor=actor,
        framework=framework,
        payload=partial_payload,
        subcommand=subcommand,
    )
    events = _change_events(root, change)
    bundle_digest: str | None = None
    if reviewer == "code-reviewer":
        verdict = (extra_payload or {}).get("verdict")
        if isinstance(verdict, dict):
            raw_digest = verdict.get("bundle_digest")
            if isinstance(raw_digest, str):
                bundle_digest = raw_digest
    sources = sorted(
        source_name
        for source_name in approved_review_sources(
            events, reviewer, bundle_digest=bundle_digest
        )
        if not policy.participants or source_name in policy.participants
    )
    if len(sources) < policy.min_independent:
        refresh_state_after_emit(root)
        new_state = derive_state(events_path(root)).get(change).current_state  # type: ignore[union-attr]
        missing = policy.min_independent - len(sources)
        if ctx.obj.get("json"):
            click.echo(
                json_envelope(
                    command=subcommand,
                    status="pass",
                    exit_code=EXIT_OK,
                    data={
                        "change": change,
                        "reviewer": reviewer,
                        "event_emitted": "review_verdict_recorded",
                        "new_state": new_state,
                        "independent_sources": sources,
                        "min_independent": policy.min_independent,
                        "missing_independent": missing,
                    },
                )
            )
        elif not ctx.obj.get("quiet"):
            click.echo(
                f"super-harness: recorded review_verdict_recorded for {change} "
                f"(reviewer={reviewer}, source={source}); waiting for {missing} "
                f"more independent source(s) → {new_state}"
            )
        sys.exit(EXIT_OK)

    milestone_payload: dict[str, object] = {
        "source": source,
        "independent_sources": sources,
        "min_independent": policy.min_independent,
        **(extra_payload or {}),
    }
    _emit_review_event(
        root,
        change=change,
        reviewer=reviewer,
        event_type=_REVIEWER_PASS[reviewer],
        reason=reason,
        actor=actor,
        framework=framework,
        payload=milestone_payload,
        subcommand=subcommand,
    )
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
                    "event_emitted": _REVIEWER_PASS[reviewer],
                    "new_state": new_state,
                    "independent_sources": sources,
                    "min_independent": policy.min_independent,
                },
            )
        )
    elif not ctx.obj.get("quiet"):
        click.echo(
            f"super-harness: emitted {_REVIEWER_PASS[reviewer]} for {change} "
            f"(reviewer={reviewer}, sources={', '.join(sources)}) → {new_state}"
        )
    sys.exit(EXIT_OK)


def _reject_failing_checklist(verdict: dict[str, object], subcommand: str) -> None:
    """Exit EXIT_VALIDATION when an APPROVE verdict's own checklist says fail.

    A `fail` status is the reviewer's record that the change is not approvable;
    recording it as `plan_approved` / `code_review_passed` would let a
    self-contradictory verdict through the merge gate. Applies to BOTH reviewer
    branches (code-reviewer required verdict, plan-reviewer optional verdict);
    the same verdict stays valid for `review reject`.
    """
    fails = failing_items(verdict)
    if fails:
        click.echo(format_error(subcommand=subcommand,
            message=f"verdict has failing checklist item(s): {', '.join(fails)}; "
                    "an approve cannot record a failing review.",
            hint="Record it with `review reject --verdict-file <path>`, or fix the "
                 "code and re-run `review prepare` + the review."), err=True)
        sys.exit(EXIT_VALIDATION)


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

    _reject_failing_checklist(verdict, subcommand)

    required = resolve_checklist(root, reviewer)
    missing = check_coverage(verdict, required)
    if missing:
        click.echo(format_error(subcommand=subcommand,
            message=f"verdict does not cover every checklist item; missing: {', '.join(missing)}",
            hint="Every checklist item must have a status (pass/fail/na)."), err=True)
        sys.exit(EXIT_VALIDATION)

    cs = derive_state(events_path(root)).get(change)
    _validate_verdict_freshness(root, change, reviewer, verdict, base, subcommand)

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

    # B-layer (design 2026-06-25 §5.1): a high-confidence dead doc code-reference
    # blocks the code-review approve emit (the primary ③ gate). Last check before
    # return, after coverage / freshness / dispose.
    dead = [f for f in scan_doc_refs(root).findings if f.confidence == "high"]
    if dead:
        listing = "; ".join(f"{f.doc_file}:{f.line} `{f.symbol}`" for f in dead)
        click.echo(format_error(subcommand=subcommand,
            message=f"docs reference code symbol(s) that no longer resolve in source: {listing}",
            hint="Fix or remove the dead reference(s); `super-harness doc refs` lists them."),
            err=True)
        sys.exit(EXIT_VALIDATION)
    return verdict


def _validate_verdict_freshness(
    root: Path,
    change: str,
    reviewer: str,
    verdict: dict[str, object],
    base: str | None,
    subcommand: str,
) -> None:
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


@review_group.command("approve")
@click.argument("change")
@_reviewer_opt
@click.option("--reason", default="approved", help="Audit reason recorded on the event.")
@click.option("--verdict-file", default=None, help="Structured verdict file "
              "(REQUIRED for code-reviewer; see `review prepare`).")
@click.option("--base", default=None, help="Base branch for freshness check "
              "(default: policy.yaml review.base_branch, else main).")
@_source_opt
@_as_opt
@click.pass_context
def approve(ctx: click.Context, change: str, reviewer: str, reason: str,
            verdict_file: str | None, base: str | None, source: str | None,
            as_identity: str | None) -> None:
    """Record a PASS verdict: emit `plan_approved` / `code_review_passed`."""
    extra: dict[str, object] | None = None
    try:
        root = find_harness_root(Path(ctx.obj.get("workspace") or "."))
    except HarnessNotInitialized as e:
        click.echo(format_error(subcommand="review approve", message=e.message, hint=e.hint),
                   err=True)
        sys.exit(EXIT_NO_CONFIG)
    reviewed_head = _validate_prepared_target_head(root, change, reviewer, "review approve")
    if reviewer == "code-reviewer":
        verdict = _validate_code_review_verdict(
            root, change, reviewer, verdict_file, base, "review approve")
        extra = {"verdict": verdict}
    elif verdict_file:  # plan-reviewer: inline if provided, not required (advisory this slice)
        try:
            verdict = parse_verdict_file(Path(verdict_file))
        except VerdictError as e:
            click.echo(format_error(subcommand="review approve", message=str(e)), err=True)
            sys.exit(EXIT_VALIDATION)
        _reject_failing_checklist(verdict, "review approve")
        extra = {"verdict": verdict}
    if reviewed_head is not None:
        extra = {**(extra or {}), "reviewed_head": reviewed_head}
    _emit_cumulative_approve(
        ctx, change=change, reviewer=reviewer, reason=reason, as_identity=as_identity,
        source=source, extra_payload=extra,
    )


@review_group.command("reject")
@click.argument("change")
@_reviewer_opt
@click.option("--reason", default="rejected", help="Audit reason recorded on the event.")
@click.option("--verdict-file", default=None, help="Structured verdict file "
              "(inlined if provided; never required for reject).")
@_source_opt
@_as_opt
@click.pass_context
def reject(ctx: click.Context, change: str, reviewer: str, reason: str,
           verdict_file: str | None, source: str | None, as_identity: str | None) -> None:
    """Record a FAIL verdict: emit `plan_rejected` / `code_review_failed`."""
    extra: dict[str, object] | None = None
    root: Path | None = None
    if source:
        try:
            root = find_harness_root(Path(ctx.obj.get("workspace") or "."))
        except HarnessNotInitialized as e:
            click.echo(format_error(subcommand="review reject", message=e.message, hint=e.hint),
                       err=True)
            sys.exit(EXIT_NO_CONFIG)
        policy = _load_policy_or_exit(root, reviewer, "review reject")
        _validate_source_or_exit(
            policy=policy, source=source, subcommand="review reject",
            require_for_threshold=False,
        )
        extra = {"source": source}
    if verdict_file:
        try:
            extra = {**(extra or {}), "verdict": parse_verdict_file(Path(verdict_file))}
        except VerdictError as e:
            click.echo(format_error(subcommand="review reject", message=str(e)), err=True)
            sys.exit(EXIT_VALIDATION)
        if root is None:
            try:
                root = find_harness_root(Path(ctx.obj.get("workspace") or "."))
            except HarnessNotInitialized as e:
                click.echo(format_error(
                    subcommand="review reject", message=e.message, hint=e.hint
                ), err=True)
                sys.exit(EXIT_NO_CONFIG)
        if reviewer == "code-reviewer":
            verdict = extra["verdict"]
            assert isinstance(verdict, dict)
            _validate_verdict_freshness(
                root, change, reviewer, verdict, None, "review reject"
            )
        reviewed_head = _validate_prepared_target_head(
            root, change, reviewer, "review reject"
        )
        if reviewed_head is not None:
            extra["reviewed_head"] = reviewed_head
    _emit_verdict(
        ctx, subcommand="review reject", change=change, reviewer=reviewer,
        event_type=_REVIEWER_FAIL[reviewer], reason=reason, as_identity=as_identity,
        extra_payload=extra,
    )


@review_group.command("skip")
@click.argument("change")
@_reviewer_opt
@click.option("--reason", default=None, help="Audit reason recorded on the event "
              "(default: manual_skip; REQUIRED with --override).")
@click.option("--override", is_flag=True, default=False,
              help="Deliberate, disclosed override: a bare skip blocks at the merge "
                   "gate; --override (with --reason) passes-with-disclosure.")
@_source_opt
@_as_opt
@click.pass_context
def skip(ctx: click.Context, change: str, reviewer: str, reason: str | None,
         override: bool, source: str | None, as_identity: str | None) -> None:
    """Escape hatch — PASS a stuck reviewer (== approve with reason=manual_skip).

    Stamps ``payload["skipped"]=True`` so the merge-boundary disclosure can tell a
    skipped review from a real one. A bare skip of ``code-reviewer`` is a merge-gate
    blocker (``attest verify``); ``--override --reason "<why>"`` stamps
    ``payload["override"]=True`` and is treated as pass-with-disclosure (slice-2 E).
    """
    if override and not reason:
        click.echo(format_error(subcommand="review skip",
            message="--override requires --reason explaining the deliberate skip.",
            hint='e.g. review skip <c> --reviewer code-reviewer --override --reason "why".'),
            err=True)
        sys.exit(EXIT_VALIDATION)
    extra: dict[str, object] = {"skipped": True}
    if source:
        try:
            root = find_harness_root(Path(ctx.obj.get("workspace") or "."))
        except HarnessNotInitialized as e:
            click.echo(format_error(subcommand="review skip", message=e.message, hint=e.hint),
                       err=True)
            sys.exit(EXIT_NO_CONFIG)
        policy = _load_policy_or_exit(root, reviewer, "review skip")
        _validate_source_or_exit(
            policy=policy, source=source, subcommand="review skip",
            require_for_threshold=False,
        )
        extra["source"] = source
    if override:
        extra["override"] = True
    _emit_verdict(
        ctx, subcommand="review skip", change=change, reviewer=reviewer,
        event_type=_REVIEWER_PASS[reviewer], reason=reason or "manual_skip",
        as_identity=as_identity, extra_payload=extra,
    )


@review_group.command("prepare")
@click.argument("change")
@_reviewer_opt
@click.option("--base", default=None, help="Base branch for the in-scope diff "
              "(default: .harness/policy.yaml review.base_branch, else main).")
@click.pass_context
def prepare(ctx: click.Context, change: str, reviewer: str, base: str | None) -> None:
    """Compile the review bundle and per-source scoped assignments → disk.

    The harness does NOT review. It derives exact committed inspection ranges,
    source-specific options, and canonical prompts for the configured participants.
    Requires a clean in-scope tree.
    """
    try:
        root = find_harness_root(Path(ctx.obj.get("workspace") or "."))
    except HarnessNotInitialized as e:
        click.echo(format_error(subcommand="review prepare", message=e.message, hint=e.hint),
                   err=True)
        sys.exit(EXIT_NO_CONFIG)
    policy = _load_policy_or_exit(root, reviewer, "review prepare")
    cs = derive_state(events_path(root)).get(change)
    _validate_reviewer_state_or_exit(
        cs, reviewer=reviewer, subcommand="review prepare"
    )
    try:
        bundle = assemble_bundle(
            root, change_id=change, reviewer=reviewer, base=base,
            spec_plan_resolver=resolve_spec_plan_paths,
        )
    except BundleError as e:
        click.echo(format_error(subcommand="review prepare", message=str(e),
                                hint="Commit the in-scope changes, then re-run review prepare."),
                   err=True)
        sys.exit(EXIT_VALIDATION)
    declared = list(cs.scope.get("files", [])) if cs is not None else []
    try:
        bundle = compile_review_contract(
            root,
            bundle=bundle,
            policy=policy,
            events=_change_events(root, change),
            declared=declared,
        )
    except (GitScopeError, ReviewContractError) as e:
        click.echo(
            format_error(
                subcommand="review prepare",
                message=str(e),
                hint="Resolve the Git history error, then re-run review prepare.",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    bundle["review_policy"] = reviewer_policy_payload(policy)
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
