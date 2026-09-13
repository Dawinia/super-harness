"""`super-harness plan` — plain-mode plan-phase emitters (HG-13): `ready` + `redeclare`.

`plan ready <slug> [--scope <files-yaml>] [--tier-hint <t>]`
(cli-command-surface §418) manually emits `plan_ready`, advancing
INTENT_DECLARED / PLAN_REJECTED → AWAITING_PLAN_REVIEW. It is the plain-mode
counterpart to the framework adapters' automatic plan-artifact observation:
without it a `plain` change `change start`-ed into INTENT_DECLARED has no CLI
verb to reach AWAITING_PLAN_REVIEW, so a cold-start change is stuck at the very
first lifecycle stage (the HG-13 self-host blocker). Emit is STRICT — an illegal
transition (e.g. from PLAN_APPROVED) is rejected and nothing is appended.

`plan redeclare <slug> [--reason <text>]` emits `plan_redeclared`, rewinding a
change from any active (non-terminal) state back to INTENT_DECLARED. It is the
late-stage scope-expansion counterpart to `plan ready`: after `redeclare`, the
scope is re-declared via `plan ready <slug> --scope @new`, which re-routes through
AWAITING_PLAN_REVIEW and the plan scope-adherence review (plan + code review
deliberately re-run — there is no silent scope-amend-without-review path). Without
it, expanding scope late (e.g. from READY_TO_MERGE) forces a `change abandon` +
new-slug workaround. Emit is STRICT — a terminal state (ARCHIVED/ABANDONED) or a
not-yet-started slug is rejected with nothing appended. Exit codes: 0 / 2 / 3.
`--reason` is optional (mirrors `change abandon --reason`); when supplied it is
recorded on the event and the reducer appends it to `redeclaration_history`.
Reconcile note: cli-command-surface §418 predates this verb; the spec's `plan`
CLI signature should grow `redeclare` (same divergence-note convention as the
`--tier-hint` flag below).

The payload carries the lifecycle-event-model §3.2 fields the reducer already
consumes (reducer.py): `scope` ({files: [...]}),
`tier_hint` (Micro/Normal/Large → cs.tier). Both are optional and omitted
from the payload when not supplied.

Reconcile note: cli-command-surface §418 lists the signature as
`plan ready <slug> [--scope <files-yaml>]` and the exit codes
as 0/1/2/3/5. We additionally expose `--tier-hint` because the lifecycle §3.2
payload schema includes `tier_hint` and the reducer already maps it onto
`cs.tier` (consumed by the anchor / verification tier policy) — the spec's CLI
signature should grow this flag. EXIT_VALIDATION=2 covers both an illegal
lifecycle transition and a malformed `--scope` (bad yaml / unreadable `@file`),
per the house convention used by the sibling emitters.

Exit codes: 0 ok / 2 illegal transition or bad `--scope` / 3 no `.harness/`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import click
import yaml

from super_harness.cli.errors import format_error
from super_harness.cli.output import json_envelope
from super_harness.core.approval import (
    ApprovalError,
    make_plan_subject,
    recognition_contract_active,
)
from super_harness.core.clock import utc_now_iso
from super_harness.core.emit_validation import EmitPreconditionError
from super_harness.core.events import Actor, Event
from super_harness.core.paths import (
    HarnessNotInitialized,
    canonical_relpath,
    events_path,
    find_harness_root,
)
from super_harness.core.post_emit import refresh_state_after_emit
from super_harness.core.reducer import derive_state
from super_harness.core.state import ChangeState
from super_harness.core.ulid import new_event_id
from super_harness.core.writer import EventWriter
from super_harness.exit_codes import EXIT_NO_CONFIG, EXIT_OK, EXIT_VALIDATION

# tier_hint enum: Micro / Normal / Large. Kept as a literal Choice so a typo is
# rejected at parse time rather than silently writing an unrecognised tier.
_TIER_CHOICES = ["Micro", "Normal", "Large"]


class _ScopeError(ValueError):
    """A `--scope` value that could not be resolved into a list of files."""


@click.group("plan")
def plan_group() -> None:
    """Plan-phase lifecycle verbs (plain-mode manual emit)."""


def _resolve_scope_files(raw: str) -> list[str]:
    """Parse the `--scope` value into a list of file paths.

    `raw` is either an inline yaml list (`"[a.py, b.py]"` / `"- a.py\\n- b.py"`)
    or `@<path>` to read that yaml from disk. The parsed value MUST be a yaml
    sequence — a mapping or scalar is rejected so the payload's `scope.files`
    shape stays predictable for the reducer / scope-vs-plan sensor.
    """
    if raw.startswith("@"):
        path = Path(raw[1:])
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise _ScopeError(f"cannot read --scope file {str(path)!r}: {exc}") from exc
    else:
        text = raw
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise _ScopeError(f"--scope is not valid yaml: {exc}") from exc
    if not isinstance(parsed, list):
        raise _ScopeError(f"--scope must be a yaml list of files, got {type(parsed).__name__}")
    return [str(item) for item in parsed]


def _detect_plan_artifacts(root: Path, slug: str, scope_files: list[str]) -> list[str]:
    """Plan artifacts = declared-scope files that are `.md` BEFORE and AFTER
    canonicalization (case-insensitive) and whose frontmatter `change:` equals `slug`.

    Source (`.py`) can never match (not `.md`). A `docs/plans/c.md` symlink to a
    `src/x.py` is rejected by the post-canonicalization `.md` check — this is what
    keeps the recorded list source-free, so the gate can trust it. Records the
    canonical repo-relative path. Unreadable files are skipped; never raises.
    """
    from super_harness.core.frontmatter import split_frontmatter

    out: list[str] = []
    for f in scope_files:
        if not f.lower().endswith(".md"):
            continue
        rel = canonical_relpath(root, f)
        if rel is None or not rel.lower().endswith(".md"):
            continue
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        fm = split_frontmatter(text)
        if fm is not None and fm[0].get("change") == slug:
            out.append(rel)
    return out


def _warn_revoked_plan_artifacts(outgoing: list[str], prev: ChangeState | None) -> None:
    """Say out loud when this emit leaves `plan_artifacts` empty after it held entries.

    The reducer PRESERVES the previous `scope` when a `plan_ready` payload omits it,
    but ALWAYS REPLACES `plan_artifacts` — an empty re-submit revokes prior
    authorization on purpose (reducer.py). The asymmetry is easy to miss: while a
    change sits in PLAN_REJECTED the gate grants a carve-out letting exactly those
    recorded plan docs be edited, so an emit carrying no artifacts silently removes
    the permission needed to revise the plan the reject loop asked you to revise.

    The trigger is deliberately about the OUTCOME, not about how it was reached:
    `--scope` omitted entirely and `--scope` passed without any frontmatter-marked
    plan doc land on the identical empty list, so one condition covers both.

    A deliberate revocation is legitimate, so this warns rather than refuses.
    """
    if outgoing or prev is None or not prev.plan_artifacts:
        return
    click.echo(
        "warning: this `plan ready` records no plan artifacts, revoking the previously "
        f"recorded ones ({', '.join(prev.plan_artifacts)}) — the PLAN_REJECTED gate "
        "carve-out that authorizes revising those plan docs is now gone (the declared "
        "scope itself is unaffected). This emit has already landed, so there is no "
        "re-run of this command from here: the carve-out returns the next time you "
        "emit `plan ready` with `--scope` naming those plan document(s), each carrying "
        "`change:` frontmatter naming this change — reach that point either through "
        "the next plan reject, or immediately via `super-harness plan redeclare "
        f"{prev.change_id}`.",
        err=True,
    )


@plan_group.command("ready")
@click.argument("slug")
@click.option(
    "--scope",
    "scope_raw",
    default=None,
    help="scope.files as an inline yaml list, or `@<path>` to read the yaml from a file.",
)
@click.option(
    "--tier-hint",
    type=click.Choice(_TIER_CHOICES),
    default=None,
    help="Optional tier estimate (Micro/Normal/Large); recorded as tier_hint → cs.tier.",
)
@click.option(
    "--plan",
    "plan_path",
    default=None,
    help="Plan artifact to snapshot into a new-contract plan subject.",
)
@click.option(
    "--commitment",
    "commitments",
    multiple=True,
    help="Binding commitment as ID=TEXT (repeatable).",
)
@click.pass_context
def ready(
    ctx: click.Context,
    slug: str,
    scope_raw: str | None,
    tier_hint: str | None,
    plan_path: str | None,
    commitments: tuple[str, ...],
) -> None:
    """Emit `plan_ready` (INTENT_DECLARED / PLAN_REJECTED → AWAITING_PLAN_REVIEW)."""
    try:
        root = find_harness_root(Path(ctx.obj.get("workspace") or "."))
    except HarnessNotInitialized as e:
        click.echo(
            format_error(subcommand="plan ready", message=e.message, hint=e.hint),
            err=True,
        )
        sys.exit(EXIT_NO_CONFIG)

    cs = derive_state(events_path(root)).get(slug)
    try:
        active_recognition = recognition_contract_active(root)
    except ApprovalError as exc:
        click.echo(format_error(subcommand="plan ready", message=str(exc)), err=True)
        sys.exit(EXIT_VALIDATION)
    if active_recognition and plan_path is None:
        click.echo(
            format_error(
                subcommand="plan ready",
                message="active review recognition requires a complete plan subject",
                hint="Pass --plan and at least one --commitment.",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    if active_recognition and not commitments:
        click.echo(
            format_error(
                subcommand="plan ready",
                message="active review recognition requires plan commitments",
                hint="Pass one or more --commitment ID=TEXT values.",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)

    payload: dict[str, object] = {}
    # The artifacts THIS emit will carry. Stays `[]` when `--scope` is omitted, and
    # also when a passed scope happens to contain no marked plan doc — the reducer
    # replaces the stored list either way, so both are the same revocation.
    recorded_artifacts: list[str] = []
    if scope_raw is not None:
        try:
            files = _resolve_scope_files(scope_raw)
        except _ScopeError as e:
            click.echo(
                format_error(
                    subcommand="plan ready",
                    message=str(e),
                    hint="`--scope` takes a yaml list of files, or `@<path>` to read it from disk.",
                ),
                err=True,
            )
            sys.exit(EXIT_VALIDATION)
        payload["scope"] = {"files": files}
        # Record the change's plan artifacts (marked `.md` in the declared scope) so
        # the PLAN_REJECTED gate carve-out can authorize revising them (HG-PLAN-AUTHORING).
        recorded_artifacts = _detect_plan_artifacts(root, slug, files)
        if recorded_artifacts:
            payload["plan_artifacts"] = recorded_artifacts
    if tier_hint is not None:
        payload["tier_hint"] = tier_hint

    if plan_path is None and commitments:
        click.echo(
            format_error(
                subcommand="plan ready",
                message="--commitment requires --plan",
                hint="Snapshot the plan and its commitments together.",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    if plan_path is not None:
        try:
            commitment_records = []
            for raw in commitments:
                if "=" not in raw:
                    raise ApprovalError("--commitment must use ID=TEXT")
                identifier, text = raw.split("=", 1)
                if not identifier or not text:
                    raise ApprovalError("--commitment must have non-empty ID and text")
                commitment_records.append({"id": identifier, "text": text})
            rel_plan = plan_path
            # The plan is always the first adopted artifact.  If the repository
            # contains the matching design and foundations documents, include
            # them as independent snapshots rather than leaving them as mutable
            # links.  A caller can still use a minimal plan-only subject in a
            # small fixture repository.
            adopted_artifacts: list[tuple[str, str]] = [(rel_plan, "plan")]
            for candidate, role in (
                (f"docs/plans/{slug}-spec.md", "spec"),
                ("docs/product-foundations.md", "foundation"),
            ):
                if (root / candidate).is_file():
                    adopted_artifacts.append((candidate, role))
            payload["plan_subject"] = make_plan_subject(
                root,
                change_id=slug,
                artifacts=adopted_artifacts,
                commitments=commitment_records,
            )
        except ApprovalError as exc:
            click.echo(
                format_error(
                    subcommand="plan ready",
                    message=str(exc),
                    hint="Pass a readable plan path and commitments as ID=TEXT.",
                ),
                err=True,
            )
            sys.exit(EXIT_VALIDATION)

    framework = cs.framework if cs is not None else "plain"  # like the sibling emitters
    ev = Event(
        event_id=new_event_id(),
        type="plan_ready",
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
                subcommand="plan ready",
                message=str(e),
                hint="`plan_ready` is only legal from INTENT_DECLARED or PLAN_REJECTED.",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    refresh_state_after_emit(root)
    _warn_revoked_plan_artifacts(recorded_artifacts, cs)

    new_cs = derive_state(events_path(root)).get(slug)
    new_state = new_cs.current_state if new_cs is not None else None
    if ctx.obj.get("json"):
        click.echo(
            json_envelope(
                command="plan ready",
                status="pass",
                exit_code=EXIT_OK,
                data={
                    "change": slug,
                    "event_emitted": "plan_ready",
                    "new_state": new_state,
                },
            )
        )
    elif not ctx.obj.get("quiet"):
        click.echo(f"super-harness: emitted plan_ready for {slug} → {new_state}")
    sys.exit(EXIT_OK)


@plan_group.command("redeclare")
@click.argument("slug")
@click.option(
    "--reason",
    default="",
    help="Optional reason for reopening the change (recorded in redeclaration_history).",
)
@click.option(
    "--plan",
    "plan_path",
    default=None,
    help="Submit a revision candidate as a new-contract plan subject.",
)
@click.option(
    "--commitment",
    "commitments",
    multiple=True,
    help="Binding revision commitment as ID=TEXT (repeatable).",
)
@click.pass_context
def redeclare(
    ctx: click.Context,
    slug: str,
    reason: str,
    plan_path: str | None,
    commitments: tuple[str, ...],
) -> None:
    """Emit `plan_redeclared` (any active state → INTENT_DECLARED).

    Rewinds a change to the first lifecycle stage so its scope can be
    (re)declared via a subsequent `plan ready <slug> --scope @new` — which routes
    back through AWAITING_PLAN_REVIEW and the plan scope-adherence review. This is
    the late-stage scope-expansion counterpart to `plan ready`: `redeclare` rewinds
    to before plan-ready, `ready` re-advances. Strict emit — a terminal state
    (ARCHIVED/ABANDONED) or a not-yet-started slug is an illegal transition,
    rejected with nothing appended.
    """
    try:
        root = find_harness_root(Path(ctx.obj.get("workspace") or "."))
    except HarnessNotInitialized as e:
        click.echo(
            format_error(subcommand="plan redeclare", message=e.message, hint=e.hint),
            err=True,
        )
        sys.exit(EXIT_NO_CONFIG)

    cs = derive_state(events_path(root)).get(slug)
    framework = cs.framework if cs is not None else "plain"  # like the sibling emitter
    payload: dict[str, object] = {}
    if reason:
        payload["reason"] = reason
    event_type = "plan_redeclared"
    if plan_path is None and commitments:
        click.echo(
            format_error(
                subcommand="plan redeclare",
                message="--commitment requires --plan",
                hint="Snapshot the revised plan and its commitments together.",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    if plan_path is not None:
        try:
            commitment_records = []
            for raw in commitments:
                if "=" not in raw:
                    raise ApprovalError("--commitment must use ID=TEXT")
                identifier, text = raw.split("=", 1)
                if not identifier or not text:
                    raise ApprovalError("--commitment must have non-empty ID and text")
                commitment_records.append({"id": identifier, "text": text})
            adopted_artifacts: list[tuple[str, str]] = [(plan_path, "plan")]
            for candidate, role in (
                (f"docs/plans/{slug}-spec.md", "spec"),
                ("docs/product-foundations.md", "foundation"),
            ):
                if (root / candidate).is_file():
                    adopted_artifacts.append((candidate, role))
            subject = make_plan_subject(
                root,
                change_id=slug,
                artifacts=adopted_artifacts,
                commitments=commitment_records,
                prior_approval=(
                    cs.effective_approval.get("approval_id")
                    if cs is not None and isinstance(cs.effective_approval, dict)
                    else None
                ),
            )
            payload["plan_subject"] = subject
            payload["prior_approval"] = (
                cs.effective_approval.get("approval_id")
                if cs is not None and isinstance(cs.effective_approval, dict)
                else None
            )
            event_type = "plan_revision_submitted"
        except ApprovalError as exc:
            click.echo(
                format_error(
                    subcommand="plan redeclare",
                    message=str(exc),
                    hint="Pass a readable plan path and commitments as ID=TEXT.",
                ),
                err=True,
            )
            sys.exit(EXIT_VALIDATION)
    ev = Event(
        event_id=new_event_id(),
        type=event_type,
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
                subcommand="plan redeclare",
                message=str(e),
                hint="`plan_redeclared` is only legal from an active (non-terminal) "
                "state — the change must already be started and not ARCHIVED/ABANDONED.",
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
                command="plan redeclare",
                status="pass",
                exit_code=EXIT_OK,
                data={
                    "change": slug,
                    "event_emitted": event_type,
                    "new_state": new_state,
                },
            )
        )
    elif not ctx.obj.get("quiet"):
        click.echo(f"super-harness: emitted plan_redeclared for {slug} → {new_state}")
    sys.exit(EXIT_OK)


@plan_group.command("withdraw")
@click.argument("slug")
@click.option("--candidate", required=True, help="Exact pending candidate subject id.")
@click.option("--reason", required=True, help="Why this unapproved candidate is not adopted.")
@click.pass_context
def withdraw(ctx: click.Context, slug: str, candidate: str, reason: str) -> None:
    """Withdraw one pending or rejected plan revision without approving it."""
    try:
        root = find_harness_root(Path(ctx.obj.get("workspace") or "."))
    except HarnessNotInitialized as exc:
        click.echo(
            format_error(subcommand="plan withdraw", message=exc.message, hint=exc.hint),
            err=True,
        )
        sys.exit(EXIT_NO_CONFIG)
    cs = derive_state(events_path(root)).get(slug)
    pending = cs.pending_revision if cs is not None else None
    if not isinstance(pending, dict) or pending.get("candidate_id") != candidate:
        click.echo(
            format_error(
                subcommand="plan withdraw",
                message=f"candidate {candidate!r} is not the current pending revision",
                hint="Use `super-harness status` and name the exact candidate id.",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    if pending.get("status") == "withdrawn":
        if ctx.obj.get("json"):
            click.echo(
                json_envelope(
                    command="plan withdraw",
                    status="pass",
                    exit_code=EXIT_OK,
                    data={"change": slug, "candidate": candidate, "idempotent": True},
                )
            )
        elif not ctx.obj.get("quiet"):
            click.echo(f"super-harness: candidate {candidate} is already withdrawn")
        sys.exit(EXIT_OK)
    if pending.get("status") not in {"pending", "rejected"}:
        click.echo(
            format_error(
                subcommand="plan withdraw",
                message="only pending or rejected candidates can be withdrawn",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    if cs is None or not isinstance(cs.effective_approval, dict):
        click.echo(
            format_error(
                subcommand="plan withdraw",
                message="a revision can be withdrawn only while an effective approval exists",
                hint="First-plan rejection needs a new plan submission, not withdrawal.",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    ev = Event(
        event_id=new_event_id(),
        type="plan_withdrawn",
        change_id=slug,
        timestamp=utc_now_iso(),
        actor=Actor(type="human", identifier="cli"),
        framework=cs.framework if cs is not None else "plain",
        payload={
            "candidate_id": candidate,
            "reason": reason,
            "effective_approval": (
                cs.effective_approval.get("approval_id")
                if cs is not None and isinstance(cs.effective_approval, dict)
                else None
            ),
        },
    )
    try:
        EventWriter(events_path(root)).emit(ev)
    except EmitPreconditionError as exc:
        click.echo(format_error(subcommand="plan withdraw", message=str(exc)), err=True)
        sys.exit(EXIT_VALIDATION)
    refresh_state_after_emit(root)
    new_state = derive_state(events_path(root)).get(slug)
    state_name = new_state.current_state if new_state is not None else None
    if ctx.obj.get("json"):
        click.echo(
            json_envelope(
                command="plan withdraw",
                status="pass",
                exit_code=EXIT_OK,
                data={"change": slug, "candidate": candidate, "new_state": state_name},
            )
        )
    elif not ctx.obj.get("quiet"):
        click.echo(f"super-harness: withdrew plan candidate {candidate} for {slug}")
    sys.exit(EXIT_OK)
