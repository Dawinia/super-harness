"""Compile review contracts and record caller-owned reviewer evidence.

super-harness never runs a reviewer. Automated evidence enters through frozen runs
and imported receipts; human evidence enters through a nonce-bound TTY confirmation.
Direct ``approve`` and ``reject`` remain only as fail-loud compatibility commands.
``skip`` is the explicit, disclosed escape hatch.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

import click

from super_harness.adapters.registry import resolve_spec_plan_paths
from super_harness.adapters.reviewer.base import ReviewerProtocolError
from super_harness.adapters.reviewer.registry import get_reviewer_protocol
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
from super_harness.core.review_bundle import BundleError, assemble_bundle
from super_harness.core.review_verdict import (
    VerdictError,
    check_disposed,
    derive_open_findings,
    failing_items,
    parse_verdict_file,
    read_change_events,
    review_verdict_json_schema,
    validate_verdict_mapping,
)
from super_harness.core.scope_match import (
    GitScopeError,
    resolve_commit,
    working_tree_dirty,
)
from super_harness.core.ulid import new_event_id
from super_harness.core.writer import EventWriter
from super_harness.engineering.review_contract import ReviewContractError, compile_review_contract
from super_harness.engineering.review_governance import (
    ReviewGovernance,
    ReviewGovernanceError,
    load_review_governance,
)
from super_harness.engineering.review_profiles import (
    ReviewProducerProfile,
    ReviewProfilesError,
    load_review_profiles,
    resolve_role_profiles,
)
from super_harness.engineering.review_runs import (
    ReviewExecutionState,
    ReviewRoundState,
    ReviewRunState,
    derive_review_execution,
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
    """Compile contracts, import receipts, or disclose a review skip."""


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
    help="Reviewer source label from review-governance.yaml.",
)

_REVIEWER_STATES: dict[str, set[str]] = {
    "plan-reviewer": {"AWAITING_PLAN_REVIEW"},
    "code-reviewer": {"AWAITING_CODE_REVIEW", "CODE_REVIEW_REJECTED"},
}


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


def _reviewer_dir(root: Path, change: str, reviewer: str) -> Path:
    return pending_reviews_dir(root, change) / reviewer


def _draft_packet_path(root: Path, change: str, reviewer: str) -> Path:
    return _reviewer_dir(root, change, reviewer) / "draft.packet.json"


def _load_governance_or_exit(root: Path, subcommand: str) -> ReviewGovernance:
    try:
        return load_review_governance(root)
    except ReviewGovernanceError as exc:
        click.echo(format_error(subcommand=subcommand, message=str(exc)), err=True)
        sys.exit(EXIT_VALIDATION)


def _resolve_profiles_or_exit(
    root: Path, governance: ReviewGovernance, reviewer: str, subcommand: str
) -> dict[str, ReviewProducerProfile]:
    try:
        profiles = load_review_profiles(root)
        return dict(resolve_role_profiles(governance, profiles, reviewer))
    except ReviewProfilesError as exc:
        click.echo(format_error(subcommand=subcommand, message=str(exc)), err=True)
        sys.exit(EXIT_VALIDATION)


def _governance_payload(
    governance: ReviewGovernance, reviewer: str
) -> dict[str, object]:
    role = governance.roles[reviewer]
    return {
        "reviewer": reviewer,
        "participants": list(role.participants),
        "min_independent": role.min_independent,
        "max_automatic_rounds_per_epoch": role.max_automatic_rounds_per_epoch,
        "require_distinct_model_families": governance.require_distinct_model_families,
        "sources": {
            source: {"kind": governance.sources[source].kind}
            for source in role.participants
        },
    }


def _read_packet_or_exit(
    root: Path, change: str, reviewer: str, subcommand: str
) -> dict[str, Any]:
    path = _draft_packet_path(root, change, reviewer)
    try:
        parsed: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        click.echo(
            format_error(
                subcommand=subcommand,
                message=f"prepared review packet is unavailable: {exc}",
                hint=f"Run `super-harness review prepare {change} --reviewer {reviewer}`.",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    if not isinstance(parsed, dict):
        click.echo(
            format_error(
                subcommand=subcommand,
                message="prepared review packet must be a JSON object",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    return parsed


def _retry_contract_events(
    events: list[Event],
    execution: ReviewExecutionState,
    packet: dict[str, Any],
    reviewer: str,
) -> tuple[list[Event], ReviewRoundState | None, bool]:
    """Slice the event log so a retry recompiles the SAME frozen contract.

    When the latest round for ``reviewer`` is a closed ``execution_failed`` round
    for the packet's exact (contract_digest, target_head, profile_digest), a retry
    of the failed subset must recompile against the events *before* that round
    started — otherwise a partially-imported source shifts its incremental
    baseline and the contract digest changes. ``begin`` and ``authorize`` must
    slice identically or they disagree on whether the prepared packet is stale,
    wedging the user between the two commands (PR#79 finding #7).

    Returns ``(contract_events, retry_anchor_round, retrying)``.
    """

    prior_round = execution.rounds[-1] if execution.rounds else None
    retrying = bool(
        prior_round is not None
        and prior_round.status == "closed"
        and prior_round.outcome == "execution_failed"
        and prior_round.contract_digest == packet.get("contract_digest")
        and prior_round.target_head == packet.get("target_head")
        and prior_round.profile_digest == packet.get("profile_digest")
    )
    anchor = prior_round
    if retrying:
        anchor = next(
            (
                round_state
                for round_state in execution.rounds
                if round_state.outcome == "execution_failed"
                and round_state.contract_digest == packet.get("contract_digest")
                and round_state.target_head == packet.get("target_head")
                and round_state.profile_digest == packet.get("profile_digest")
            ),
            prior_round,
        )
    contract_events = events
    if retrying and anchor is not None:
        for index, event in enumerate(events):
            payload = event.payload or {}
            if (
                event.type == "review_round_started"
                and payload.get("reviewer") == reviewer
                and payload.get("round_id") == anchor.round_id
            ):
                contract_events = events[:index]
                break
    return contract_events, anchor, retrying


def _current_packet_or_exit(
    root: Path,
    *,
    change: str,
    reviewer: str,
    packet: dict[str, Any],
    governance: ReviewGovernance,
    profiles: dict[str, ReviewProducerProfile],
    subcommand: str,
    contract_events: list[Event] | None = None,
) -> dict[str, Any]:
    cs = derive_state(events_path(root)).get(change)
    declared = list(cs.scope.get("files", [])) if cs is not None else []
    try:
        current = assemble_bundle(
            root,
            change_id=change,
            reviewer=reviewer,
            base=str(packet.get("base") or governance.base_branch),
            spec_plan_resolver=resolve_spec_plan_paths,
        )
        current["review_governance"] = _governance_payload(governance, reviewer)
        current = compile_review_contract(
            root,
            bundle=current,
            governance=governance,
            profiles=profiles,
            events=(
                contract_events
                if contract_events is not None
                else _change_events(root, change)
            ),
            declared=declared,
        )
    except (BundleError, GitScopeError, ReviewContractError) as exc:
        click.echo(
            format_error(
                subcommand=subcommand,
                message=f"cannot validate prepared review packet: {exc}",
                hint="Commit in-scope changes and run review prepare again.",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    if current.get("contract_digest") != packet.get("contract_digest"):
        click.echo(
            format_error(
                subcommand=subcommand,
                message="prepared review packet is stale",
                hint="Governance, profiles, checklist, prompt, target, or Git state changed; "
                "run review prepare again.",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    return current


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


def _block_direct_verdict_protocol(root: Path, subcommand: str) -> None:
    governance_path = root / ".harness" / "review-governance.yaml"
    legacy_path = root / ".harness" / "policy.yaml"
    if legacy_path.is_file() and not governance_path.is_file():
        click.echo(
            format_error(
                subcommand=subcommand,
                message="legacy .harness/policy.yaml cannot record new review evidence",
                hint="Replace it with review-governance.yaml and use the receipt workflow.",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    click.echo(
        format_error(
            subcommand=subcommand,
            message="direct review approve/reject is disabled by the review execution protocol",
            hint=(
                "Automated sources use `review result import`; humans use "
                "`review human draft` then TTY-only `review human confirm`."
            ),
        ),
        err=True,
    )
    sys.exit(EXIT_VALIDATION)


@review_group.command("approve")
@click.argument("change")
@_reviewer_opt
@click.option("--reason", default="approved", help="Compatibility option; no evidence is recorded.")
@click.option(
    "--verdict-file",
    default=None,
    help="Compatibility option; use `review result import`.",
)
@click.option("--base", default=None, help="Compatibility option; direct evidence is disabled.")
@_source_opt
@_as_opt
@click.pass_context
def approve(ctx: click.Context, change: str, reviewer: str, reason: str,
            verdict_file: str | None, base: str | None, source: str | None,
            as_identity: str | None) -> None:
    """Fail loudly: direct PASS evidence is disabled; import a receipt."""
    del change, reviewer, reason, verdict_file, base, source, as_identity
    try:
        root = find_harness_root(Path(ctx.obj.get("workspace") or "."))
    except HarnessNotInitialized as e:
        click.echo(format_error(subcommand="review approve", message=e.message, hint=e.hint),
                   err=True)
        sys.exit(EXIT_NO_CONFIG)
    _block_direct_verdict_protocol(root, "review approve")


@review_group.command("reject")
@click.argument("change")
@_reviewer_opt
@click.option("--reason", default="rejected", help="Compatibility option; no evidence is recorded.")
@click.option(
    "--verdict-file",
    default=None,
    help="Compatibility option; use `review result import`.",
)
@_source_opt
@_as_opt
@click.pass_context
def reject(ctx: click.Context, change: str, reviewer: str, reason: str,
           verdict_file: str | None, source: str | None, as_identity: str | None) -> None:
    """Fail loudly: direct FAIL evidence is disabled; import a receipt."""
    del change, reviewer, reason, verdict_file, source, as_identity
    try:
        root = find_harness_root(Path(ctx.obj.get("workspace") or "."))
    except HarnessNotInitialized as e:
        click.echo(
            format_error(subcommand="review reject", message=e.message, hint=e.hint),
            err=True,
        )
        sys.exit(EXIT_NO_CONFIG)
    _block_direct_verdict_protocol(root, "review reject")


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
        governance = _load_governance_or_exit(root, "review skip")
        participants = governance.roles[reviewer].participants
        if source not in participants:
            click.echo(
                format_error(
                    subcommand="review skip",
                    message=f"source {source!r} is not a participant for {reviewer}",
                    hint=f"Configured participants: {', '.join(participants)}",
                ),
                err=True,
            )
            sys.exit(EXIT_VALIDATION)
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
              "(default: tracked review governance, else main).")
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
    governance = _load_governance_or_exit(root, "review prepare")
    if reviewer not in governance.roles:
        click.echo(
            format_error(
                subcommand="review prepare",
                message=f"review role {reviewer!r} is not configured",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    profiles = _resolve_profiles_or_exit(
        root, governance, reviewer, "review prepare"
    )
    cs = derive_state(events_path(root)).get(change)
    _validate_reviewer_state_or_exit(
        cs, reviewer=reviewer, subcommand="review prepare"
    )
    try:
        bundle = assemble_bundle(
            root,
            change_id=change,
            reviewer=reviewer,
            base=base or governance.base_branch,
            spec_plan_resolver=resolve_spec_plan_paths,
        )
    except BundleError as e:
        click.echo(format_error(subcommand="review prepare", message=str(e),
                                hint="Commit the in-scope changes, then re-run review prepare."),
                   err=True)
        sys.exit(EXIT_VALIDATION)
    declared = list(cs.scope.get("files", [])) if cs is not None else []
    bundle["review_governance"] = _governance_payload(governance, reviewer)
    try:
        bundle = compile_review_contract(
            root,
            bundle=bundle,
            governance=governance,
            profiles=profiles,
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
    out_dir = _reviewer_dir(root, change, reviewer)
    out_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = _draft_packet_path(root, change, reviewer)
    bundle_path.write_text(json.dumps(bundle, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if ctx.obj.get("json"):
        click.echo(json_envelope(command="review prepare", status="pass", exit_code=EXIT_OK,
                                 data={"change": change, "reviewer": reviewer,
                                       "bundle_path": str(bundle_path),
                                       "bundle_digest": bundle["bundle_digest"],
                                       "contract_digest": bundle["contract_digest"],
                                       "target_head": bundle["target_head"],
                                       "source_count": len(bundle["assignments"]),
                                       "warnings": bundle["warnings"],
                                       "diff_in_scope": bundle["diff_in_scope"],
                                       "out_of_scope": bundle["out_of_scope"]}))
    elif not ctx.obj.get("quiet"):
        click.echo(f"super-harness: wrote review bundle for {change} ({reviewer}) → {bundle_path}")
        if bundle["out_of_scope"]:
            click.echo("  out-of-scope changes (review carefully):\n    "
                       + "\n    ".join(bundle["out_of_scope"]))
    sys.exit(EXIT_OK)


def _new_review_id(prefix: str) -> str:
    return f"{prefix}_{new_event_id().removeprefix('ev_')}"


def _interactive_terminal() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


@review_group.command("begin")
@click.argument("change")
@_reviewer_opt
@click.option(
    "--source",
    "sources",
    multiple=True,
    help="Retry source; repeat for the complete currently failed subset.",
)
@click.pass_context
def begin(
    ctx: click.Context, change: str, reviewer: str, sources: tuple[str, ...]
) -> None:
    """Freeze one automated round and return caller-owned invocation contracts."""

    subcommand = "review begin"
    try:
        root = find_harness_root(Path(ctx.obj.get("workspace") or "."))
    except HarnessNotInitialized as exc:
        click.echo(
            format_error(subcommand=subcommand, message=exc.message, hint=exc.hint),
            err=True,
        )
        sys.exit(EXIT_NO_CONFIG)
    cs = derive_state(events_path(root)).get(change)
    _validate_reviewer_state_or_exit(cs, reviewer=reviewer, subcommand=subcommand)
    governance = _load_governance_or_exit(root, subcommand)
    if reviewer not in governance.roles:
        click.echo(
            format_error(
                subcommand=subcommand,
                message=f"review role {reviewer!r} is not configured",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    profiles = _resolve_profiles_or_exit(root, governance, reviewer, subcommand)
    packet = _read_packet_or_exit(root, change, reviewer, subcommand)
    events = _change_events(root, change)
    execution = derive_review_execution(events, reviewer)
    contract_events, retry_anchor_round, retrying_frozen_contract = (
        _retry_contract_events(events, execution, packet, reviewer)
    )
    packet = _current_packet_or_exit(
        root,
        change=change,
        reviewer=reviewer,
        packet=packet,
        governance=governance,
        profiles=profiles,
        subcommand=subcommand,
        contract_events=contract_events,
    )
    assignments = {
        assignment["source"]: assignment
        for assignment in packet.get("assignments", [])
        if isinstance(assignment, dict)
        and isinstance(assignment.get("source"), str)
        and assignment.get("kind") == "automated"
    }
    role = governance.roles[reviewer]
    automated = tuple(
        source
        for source in role.participants
        if governance.sources[source].kind == "automated"
    )
    if not automated:
        click.echo(
            format_error(
                subcommand=subcommand,
                message=f"{reviewer} has no automated participants",
                hint="Use the interactive `review human` workflow.",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)

    if execution.epoch_id is None:
        click.echo(
            format_error(
                subcommand=subcommand,
                message=f"{reviewer} has no active review epoch",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    if execution.rounds and execution.rounds[-1].status == "open":
        pending = sorted(
            run.run_id
            for run in execution.rounds[-1].runs.values()
            if run.status == "pending"
        )
        click.echo(
            format_error(
                subcommand=subcommand,
                message=f"review round {execution.rounds[-1].round_id} is still open",
                hint=(
                    "Import or fail every pending run first: " + ", ".join(pending)
                    if pending
                    else "Close the existing round before beginning another."
                ),
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)

    same_contract = bool(
        execution.rounds
        and execution.rounds[-1].contract_digest == packet["contract_digest"]
        and execution.rounds[-1].target_head == packet["target_head"]
        and execution.rounds[-1].profile_digest == packet["profile_digest"]
    )
    retained = set(execution.retained_sources if same_contract else ())
    required_sources = tuple(source for source in automated if source not in retained)
    if not required_sources:
        click.echo(
            format_error(
                subcommand=subcommand,
                message="all automated participants already have retained results",
                hint="Import/fail the open run or prepare after the next committed change.",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    if len(set(sources)) != len(sources):
        click.echo(
            format_error(subcommand=subcommand, message="--source values must be unique"),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    selected = tuple(sources) if sources else required_sources
    if set(selected) != set(required_sources):
        click.echo(
            format_error(
                subcommand=subcommand,
                message="selected sources do not match the complete required retry set",
                hint=f"Required sources: {', '.join(required_sources)}.",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)

    needs_authorization = (
        execution.automatic_rounds_used
        >= role.max_automatic_rounds_per_epoch
        or any(profiles[source].cost_class == "expensive" for source in selected)
    )
    authorization_id: str | None = None
    if needs_authorization:
        for authorization in execution.authorizations:
            if (
                authorization.consumed_by_round_id is None
                and authorization.contract_digest == packet["contract_digest"]
                and authorization.profile_digest == packet["profile_digest"]
                and authorization.sources == tuple(sorted(selected))
            ):
                authorization_id = authorization.authorization_id
                break
        if authorization_id is None:
            click.echo(
                format_error(
                    subcommand=subcommand,
                    message="this automated round requires one-shot human authorization",
                    hint=(
                        "Use an interactive TTY: `super-harness review authorize "
                        f"{change} --reviewer {reviewer} --reason <why>` ."
                    ),
                ),
                err=True,
            )
            sys.exit(EXIT_VALIDATION)

    round_id = _new_review_id("round")
    round_dir = _reviewer_dir(root, change, reviewer) / "rounds" / round_id
    run_payloads: list[dict[str, object]] = []
    output: list[dict[str, object]] = []
    frozen_runs: list[dict[str, object]] = []
    try:
        for source in selected:
            assignment = assignments.get(source)
            if assignment is None:
                raise ReviewerProtocolError(
                    f"prepared packet has no automated assignment for {source!r}"
                )
            profile = profiles[source]
            run_id = _new_review_id("run")
            run_dir = round_dir / "runs" / run_id
            run_dir.mkdir(parents=True, exist_ok=False)
            prompt_path = run_dir / "prompt.md"
            schema_path = run_dir / "verdict.schema.json"
            binding = (
                "Frozen result binding (copy these values exactly into the JSON result):\n"
                f"run_id: {run_id}\n"
                f"source: {source}\n"
                f"target_head: {packet['target_head']}\n"
                f"contract_digest: {packet['contract_digest']}\n"
                f"bundle_digest: {packet['bundle_digest']}\n"
                "scope_sufficient: true unless the assigned inspection target cannot "
                "support a complete review.\n\n"
            )
            prompt = binding + str(assignment["prompt"])
            prompt_path.write_text(prompt, encoding="utf-8")
            schema_path.write_text(
                json.dumps(
                    review_verdict_json_schema(list(packet["checklist"])),
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            adapter = get_reviewer_protocol(profile.protocol)
            invocation = adapter.compile_invocation(
                workspace=root,
                run_dir=run_dir,
                prompt_path=prompt_path,
                schema_path=schema_path,
                model=profile.model,
                agent_options=profile.agent_options,
            )
            invocation_payload: dict[str, object] = {
                "protocol": invocation.protocol,
                "argv": list(invocation.argv),
                "cwd": str(invocation.cwd),
                "stdin_path": str(invocation.stdin_path),
                "output_path": str(invocation.output_path),
                "capture_stdout": invocation.capture_stdout,
                "stdout_path": (
                    str(invocation.stdout_path)
                    if invocation.stdout_path is not None
                    else None
                ),
                "telemetry_path": (
                    str(invocation.telemetry_path)
                    if invocation.telemetry_path is not None
                    else None
                ),
                "requested_model": invocation.requested_model,
                "requested_options": invocation.requested_options,
            }
            invocation_path = run_dir / "invocation.json"
            invocation_path.write_text(
                json.dumps(invocation_payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            prompt_digest = sha256(prompt.encode("utf-8")).hexdigest()
            run_payload: dict[str, object] = {
                "run_id": run_id,
                "source": source,
                "protocol": profile.protocol,
                "requested_model": profile.model,
                "requested_options": dict(profile.agent_options),
                "cost_class": profile.cost_class,
                "prompt_digest": prompt_digest,
                "invocation": invocation_payload,
            }
            run_payloads.append(run_payload)
            frozen_runs.append({**run_payload, "assignment": assignment})
            output_entry: dict[str, object] = {
                "run_id": run_id,
                "source": source,
                "invocation_path": str(invocation_path),
            }
            output_entry.update(invocation_payload)
            output.append(output_entry)
    except (OSError, ReviewerProtocolError, ValueError) as exc:
        click.echo(
            format_error(
                subcommand=subcommand,
                message=f"cannot compile reviewer invocation: {exc}",
                hint="Fix the local producer profile or installation; no round was consumed.",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)

    frozen_path = round_dir / "contract.json"
    frozen_path.write_text(
        json.dumps(
            {
                "round_id": round_id,
                "epoch_id": execution.epoch_id,
                "authorization_id": authorization_id,
                "packet": packet,
                "runs": frozen_runs,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    framework = cs.framework if cs is not None else "plain"
    _emit_review_event(
        root,
        change=change,
        reviewer=reviewer,
        event_type="review_round_started",
        reason="automated review round frozen for caller-owned execution",
        actor=Actor(type="agent", identifier=resolve_identity(root, None)),
        framework=framework,
        payload={
            "epoch_id": execution.epoch_id,
            "round_id": round_id,
            "contract_digest": packet["contract_digest"],
            "bundle_digest": packet["bundle_digest"],
            "target_head": packet["target_head"],
            "profile_digest": packet["profile_digest"],
            "authorization_id": authorization_id,
            "automatic": True,
            "retained_sources": sorted(retained),
            "required_sources": list(automated),
            "min_independent": role.min_independent,
            "require_distinct_model_families": (
                governance.require_distinct_model_families
            ),
            "checklist": list(packet["checklist"]),
            # Only the code-reviewer prompt surfaces open prior findings
            # (compile_review_contract gates that section to code-reviewer). A
            # plan-reviewer round must not freeze code-review finding ids it will
            # never be shown, or import can never dispose them and plan review
            # wedges (PR#79 finding #3).
            "open_finding_ids": (
                list(retry_anchor_round.open_finding_ids)
                if retrying_frozen_contract and retry_anchor_round is not None
                else (
                    derive_open_findings(events, change)
                    if reviewer == "code-reviewer"
                    else []
                )
            ),
            "runs": run_payloads,
            "contract_path": str(frozen_path),
        },
        subcommand=subcommand,
    )
    refresh_state_after_emit(root)
    data = {
        "change": change,
        "reviewer": reviewer,
        "round_id": round_id,
        "contract_digest": packet["contract_digest"],
        "target_head": packet["target_head"],
        "contract_path": str(frozen_path),
        "automatic_rounds_used": execution.automatic_rounds_used + 1,
        "automatic_rounds_max": role.max_automatic_rounds_per_epoch,
        "runs": output,
    }
    if ctx.obj.get("json"):
        click.echo(
            json_envelope(
                command=subcommand,
                status="pass",
                exit_code=EXIT_OK,
                data=data,
            )
        )
    elif not ctx.obj.get("quiet"):
        click.echo(
            f"super-harness: froze {round_id} for {change} ({reviewer}); "
            "super-harness did not execute a reviewer"
        )
        for run in output:
            click.echo(
                f"  {run['source']}: {run['invocation_path']} "
                f"(run_id={run['run_id']})"
            )
    sys.exit(EXIT_OK)


@review_group.command("authorize")
@click.argument("change")
@_reviewer_opt
@click.option("--source", "sources", multiple=True)
@click.option("--reason", required=True)
@click.pass_context
def authorize_round(
    ctx: click.Context,
    change: str,
    reviewer: str,
    sources: tuple[str, ...],
    reason: str,
) -> None:
    """Interactively authorize one exact expensive or over-budget round."""

    subcommand = "review authorize"
    if not _interactive_terminal():
        click.echo(
            format_error(
                subcommand=subcommand,
                message="review authorization requires an interactive TTY",
                hint="Run the command directly in a human-owned terminal; no --yes path exists.",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    try:
        root = find_harness_root(Path(ctx.obj.get("workspace") or "."))
    except HarnessNotInitialized as exc:
        click.echo(
            format_error(subcommand=subcommand, message=exc.message, hint=exc.hint),
            err=True,
        )
        sys.exit(EXIT_NO_CONFIG)
    cs = derive_state(events_path(root)).get(change)
    _validate_reviewer_state_or_exit(cs, reviewer=reviewer, subcommand=subcommand)
    governance = _load_governance_or_exit(root, subcommand)
    profiles = _resolve_profiles_or_exit(root, governance, reviewer, subcommand)
    packet = _read_packet_or_exit(root, change, reviewer, subcommand)
    events = _change_events(root, change)
    execution = derive_review_execution(events, reviewer)
    # Slice identically to `review begin` so a retry packet resolves to the same
    # contract digest in both commands (PR#79 finding #7).
    contract_events, _retry_anchor, _retrying = _retry_contract_events(
        events, execution, packet, reviewer
    )
    packet = _current_packet_or_exit(
        root,
        change=change,
        reviewer=reviewer,
        packet=packet,
        governance=governance,
        profiles=profiles,
        subcommand=subcommand,
        contract_events=contract_events,
    )
    if execution.epoch_id is None:
        click.echo(
            format_error(subcommand=subcommand, message="no active review epoch"),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    role = governance.roles[reviewer]
    automated = tuple(
        source
        for source in role.participants
        if governance.sources[source].kind == "automated"
    )
    same_contract = bool(
        execution.rounds
        and execution.rounds[-1].contract_digest == packet["contract_digest"]
        and execution.rounds[-1].target_head == packet["target_head"]
        and execution.rounds[-1].profile_digest == packet["profile_digest"]
    )
    retained = set(execution.retained_sources if same_contract else ())
    required = tuple(source for source in automated if source not in retained)
    selected = tuple(sources) if sources else required
    if not selected or len(set(selected)) != len(selected) or set(selected) != set(required):
        click.echo(
            format_error(
                subcommand=subcommand,
                message="authorization sources must match the complete required retry set",
                hint=f"Required sources: {', '.join(required) or '(none)' }.",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    needs_authorization = (
        execution.automatic_rounds_used
        >= role.max_automatic_rounds_per_epoch
        or any(profiles[source].cost_class == "expensive" for source in selected)
    )
    if not needs_authorization:
        click.echo(
            format_error(
                subcommand=subcommand,
                message="the current standard-profile round is still within its automatic budget",
                hint="Run review begin directly; no authorization is consumed.",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    for existing in execution.authorizations:
        if (
            existing.consumed_by_round_id is None
            and existing.contract_digest == packet["contract_digest"]
            and existing.profile_digest == packet["profile_digest"]
            and existing.sources == tuple(sorted(selected))
        ):
            click.echo(
                f"super-harness: authorization {existing.authorization_id} already available"
            )
            sys.exit(EXIT_OK)
    prompt = (
        f"Authorize exactly one automated {reviewer} round for "
        f"{', '.join(selected)} at {str(packet['target_head'])[:12]}?"
    )
    if not click.confirm(prompt, default=False):
        click.echo("super-harness: authorization cancelled")
        sys.exit(EXIT_VALIDATION)
    framework = cs.framework if cs is not None else "plain"
    authorization_id = new_event_id()
    event = Event(
        event_id=authorization_id,
        type="review_round_authorized",
        change_id=change,
        timestamp=utc_now_iso(),
        actor=Actor(type="human", identifier=resolve_identity(root, None)),
        framework=framework,
        payload={
            "reviewer": reviewer,
            "epoch_id": execution.epoch_id,
            "contract_digest": packet["contract_digest"],
            "profile_digest": packet["profile_digest"],
            "sources": sorted(selected),
            "reason": reason,
        },
    )
    try:
        EventWriter(events_path(root)).emit(event)
    except EmitPreconditionError as exc:
        click.echo(format_error(subcommand=subcommand, message=str(exc)), err=True)
        sys.exit(EXIT_VALIDATION)
    refresh_state_after_emit(root)
    data = {
        "change": change,
        "reviewer": reviewer,
        "authorization_id": authorization_id,
        "contract_digest": packet["contract_digest"],
        "profile_digest": packet["profile_digest"],
        "sources": sorted(selected),
    }
    if ctx.obj.get("json"):
        click.echo(
            json_envelope(
                command=subcommand,
                status="pass",
                exit_code=EXIT_OK,
                data=data,
            )
        )
    elif not ctx.obj.get("quiet"):
        click.echo(
            f"super-harness: authorized one round ({authorization_id}); "
            "authorization will be consumed by review begin"
        )
    sys.exit(EXIT_OK)


def _find_review_run_or_exit(
    execution: ReviewExecutionState, run_id: str, subcommand: str
) -> tuple[ReviewRoundState, ReviewRunState]:
    for round_state in reversed(execution.rounds):
        for run in round_state.runs.values():
            if run.run_id == run_id:
                return round_state, run
    click.echo(
        format_error(
            subcommand=subcommand,
            message=f"unknown run id for the current review epoch: {run_id!r}",
        ),
        err=True,
    )
    sys.exit(EXIT_VALIDATION)


def _model_family(model: str) -> str:
    normalized = model.lower()
    if "claude" in normalized:
        return "claude"
    if any(token in normalized for token in ("gpt", "codex", "openai", "o3", "o4")):
        return "openai"
    return normalized.split("-", 1)[0]


def _model_contradicts(requested: str, actual: str) -> bool:
    """True only when a producer-reported model genuinely conflicts with the request.

    Producers report a canonical, fully-qualified id (e.g. a dated variant such
    as ``claude-opus-4-1-20250805``) even when the profile requested a shorter
    alias or undated id (``opus``, ``claude-opus-4-1``). Per the plan, only an
    explicit *contradiction* invalidates a result; a more-specific reported id is
    an honored request, not a contradiction. Treat the pair as consistent when
    either identifier is a case-insensitive substring of the other, so only a
    disjoint pair (e.g. ``opus`` requested but ``sonnet`` reported) is rejected
    (PR#79 finding #6)."""

    req = requested.strip().lower()
    act = actual.strip().lower()
    if not req or not act:
        return False
    return req not in act and act not in req


def _aggregate_verdicts(runs: dict[str, ReviewRunState]) -> dict[str, object]:
    severity_order = {"blocker": 0, "major": 1, "minor": 2}
    checklist_status: dict[str, str] = {}
    checklist_notes: dict[str, list[str]] = {}
    findings: list[dict[str, object]] = []
    prior_by_id: dict[str, dict[str, object]] = {}
    scope_sufficient = True
    source_results: list[dict[str, object]] = []
    for source in sorted(runs):
        run = runs[source]
        verdict = run.verdict
        if not isinstance(verdict, dict):
            continue
        scope_sufficient = scope_sufficient and verdict.get("scope_sufficient", True) is True
        source_results.append(
            {
                "source": source,
                "run_id": run.run_id,
                "result_digest": run.result_digest,
                "receipt": run.receipt,
            }
        )
        for entry in verdict.get("checklist", []):
            if not isinstance(entry, dict) or not isinstance(entry.get("item"), str):
                continue
            item = entry["item"]
            status = str(entry.get("status"))
            previous = checklist_status.get(item)
            if previous != "fail":
                checklist_status[item] = "fail" if status == "fail" else status
            note = entry.get("note")
            if isinstance(note, str) and note:
                checklist_notes.setdefault(item, []).append(f"{source}: {note}")
        for finding in verdict.get("findings", []):
            if isinstance(finding, dict):
                findings.append(dict(finding))
        for disposition in verdict.get("prior_findings", []):
            if isinstance(disposition, dict) and isinstance(disposition.get("id"), str):
                prior_by_id[disposition["id"]] = dict(disposition)
    checklist = [
        {
            "item": item,
            "status": checklist_status[item],
            **(
                {"note": "; ".join(checklist_notes[item])}
                if item in checklist_notes
                else {}
            ),
        }
        for item in sorted(checklist_status)
    ]
    def finding_sort_key(finding: dict[str, object]) -> tuple[int, str, int, str]:
        line = finding.get("line")
        return (
            severity_order.get(str(finding.get("severity")), 99),
            str(finding.get("file", "")),
            line if isinstance(line, int) else 0,
            str(finding.get("id", "")),
        )

    findings.sort(key=finding_sort_key)
    return {
        "scope_sufficient": scope_sufficient,
        "checklist": checklist,
        "findings": findings,
        "prior_findings": [prior_by_id[key] for key in sorted(prior_by_id)],
        "source_results": source_results,
    }


def _close_round_if_terminal(
    root: Path,
    *,
    change: str,
    reviewer: str,
    round_id: str,
    framework: str,
) -> tuple[str | None, str | None]:
    events = _change_events(root, change)
    execution = derive_review_execution(events, reviewer)
    round_state = next(
        (item for item in execution.rounds if item.round_id == round_id), None
    )
    if round_state is None or round_state.status == "closed":
        return None, None
    if any(run.status == "pending" for run in round_state.runs.values()):
        return None, None

    required = round_state.required_sources
    latest: dict[str, ReviewRunState] = {}
    for candidate in execution.rounds:
        if (
            candidate.contract_digest != round_state.contract_digest
            or candidate.target_head != round_state.target_head
            or candidate.profile_digest != round_state.profile_digest
        ):
            continue
        for source, run in candidate.runs.items():
            latest[source] = run
    imported: dict[str, ReviewRunState] = {
        source: latest[source]
        for source in required
        if source in latest and latest[source].status == "imported"
    }
    missing = [source for source in required if source not in imported]
    try:
        current_head: str | None = resolve_commit(root)
    except GitScopeError:
        current_head = None
    target_stale = current_head != round_state.target_head
    aggregate = _aggregate_verdicts(imported)
    aggregate_checklist = aggregate["checklist"]
    has_failure = isinstance(aggregate_checklist, list) and any(
        isinstance(item, dict) and item.get("status") == "fail"
        for item in aggregate_checklist
    )
    has_rejection = aggregate["scope_sufficient"] is not True or has_failure
    if target_stale:
        outcome = "execution_failed"
    elif has_rejection:
        outcome = "rejected"
    elif not round_state.frozen_governance_complete:
        outcome = "execution_failed"
    elif missing:
        outcome = "execution_failed"
    elif len(imported) < round_state.min_independent:
        # Governance requires min_independent sources, but an automated round can
        # only import its automated participants. A role that also lists a human
        # participant (min_independent > automated count) therefore cannot be
        # approved by automated imports alone — fail closed here so the human
        # review is still required rather than silently skipped (PR#79 finding #1).
        outcome = "execution_failed"
    else:
        outcome = "approved"
        if round_state.require_distinct_model_families:
            reported = []
            for run in imported.values():
                receipt = run.receipt
                actual = receipt.get("actual_model") if isinstance(receipt, dict) else None
                if not isinstance(actual, str) or not actual:
                    outcome = "execution_failed"
                    break
                reported.append(_model_family(actual))
            if len(set(reported)) != len(reported):
                outcome = "execution_failed"
        if reviewer == "code-reviewer" and outcome == "approved":
            dead_refs = [
                finding
                for finding in scan_doc_refs(root).findings
                if finding.confidence == "high"
            ]
            if dead_refs:
                outcome = "rejected"
                aggregate_findings = aggregate.get("findings")
                if isinstance(aggregate_findings, list):
                    aggregate_findings.extend(
                        {
                            "id": f"harness/doc-ref/{index}",
                            "severity": "major",
                            "file": finding.doc_file,
                            "line": finding.line,
                            "summary": (
                                f"Documented code symbol no longer resolves: "
                                f"{finding.symbol}"
                            ),
                            "source": "harness",
                        }
                        for index, finding in enumerate(dead_refs, start=1)
                    )

    actor = Actor(type="agent", identifier="review-protocol")
    _emit_review_event(
        root,
        change=change,
        reviewer=reviewer,
        event_type="review_round_closed",
        reason=f"all issued runs terminal: {outcome}",
        actor=actor,
        framework=framework,
        payload={
            "epoch_id": execution.epoch_id,
            "round_id": round_id,
            "contract_digest": round_state.contract_digest,
            "target_head": round_state.target_head,
            "profile_digest": round_state.profile_digest,
            "outcome": outcome,
            "missing_sources": missing,
            "current_head": current_head,
            "target_stale": target_stale,
            "frozen_governance_complete": round_state.frozen_governance_complete,
        },
        subcommand="review result import",
    )
    milestone: str | None = None
    if outcome in {"approved", "rejected"}:
        milestone = (
            _REVIEWER_PASS[reviewer] if outcome == "approved" else _REVIEWER_FAIL[reviewer]
        )
        _emit_review_event(
            root,
            change=change,
            reviewer=reviewer,
            event_type=milestone,
            reason=f"complete imported reviewer set {outcome}",
            actor=actor,
            framework=framework,
            payload={
                "outcome": outcome,
                "reviewed_head": round_state.target_head,
                "contract_digest": round_state.contract_digest,
                "profile_digest": round_state.profile_digest,
                "independent_sources": sorted(imported),
                "missing_sources": missing,
                "min_independent": round_state.min_independent,
                "receipt_ids": [
                    getattr(run, "receipt", {}).get("receipt_id")
                    for run in imported.values()
                    if isinstance(getattr(run, "receipt", None), dict)
                ],
                "verdict": aggregate,
            },
            subcommand="review result import",
        )
    refresh_state_after_emit(root)
    return outcome, milestone


@review_group.group("result")
def result_group() -> None:
    """Import completed caller-owned reviewer results."""


@result_group.command("import")
@click.argument("change")
@_reviewer_opt
@click.option("--run-id", required=True, help="Frozen reviewer run identifier.")
@click.option(
    "--result-file",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
    help="Completed raw producer output file.",
)
@click.pass_context
def import_result(
    ctx: click.Context,
    change: str,
    reviewer: str,
    run_id: str,
    result_file: str,
) -> None:
    """Parse and record one completed external result; never run its producer."""

    subcommand = "review result import"
    try:
        root = find_harness_root(Path(ctx.obj.get("workspace") or "."))
    except HarnessNotInitialized as exc:
        click.echo(
            format_error(subcommand=subcommand, message=exc.message, hint=exc.hint),
            err=True,
        )
        sys.exit(EXIT_NO_CONFIG)
    cs = derive_state(events_path(root)).get(change)
    execution = derive_review_execution(_change_events(root, change), reviewer)
    round_state, run = _find_review_run_or_exit(execution, run_id, subcommand)
    raw_path = Path(result_file).resolve()
    try:
        raw_bytes = raw_path.read_bytes()
    except OSError as exc:
        click.echo(format_error(subcommand=subcommand, message=str(exc)), err=True)
        sys.exit(EXIT_VALIDATION)
    result_digest = sha256(raw_bytes).hexdigest()
    if run.status == "imported":
        if run.result_digest == result_digest:
            data = {
                "change": change,
                "reviewer": reviewer,
                "run_id": run_id,
                "result_digest": result_digest,
                "idempotent": True,
            }
            if ctx.obj.get("json"):
                click.echo(
                    json_envelope(
                        command=subcommand,
                        status="pass",
                        exit_code=EXIT_OK,
                        data=data,
                    )
                )
            elif not ctx.obj.get("quiet"):
                click.echo(f"super-harness: result {run_id} already imported")
            sys.exit(EXIT_OK)
        click.echo(
            format_error(
                subcommand=subcommand,
                message="conflicting second result for an already imported run",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    if run.status == "failed":
        click.echo(
            format_error(
                subcommand=subcommand,
                message="cannot import a result after the run was recorded failed",
                hint="Begin a new explicit retry round.",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    try:
        current_head = resolve_commit(root)
    except GitScopeError as exc:
        click.echo(
            format_error(
                subcommand=subcommand,
                message=f"cannot resolve current HEAD: {exc}",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    if current_head != round_state.target_head:
        click.echo(
            format_error(
                subcommand=subcommand,
                message="current HEAD no longer matches the frozen review target",
                hint="Commit the intended target, prepare a new packet, and begin a new round.",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    # A matching HEAD is not enough for code review: uncommitted in-scope edits
    # made while the reviewer ran are outside the committed diff the reviewer
    # inspected. `review prepare` checks this once, but the tree can go dirty
    # between prepare and import, so re-check here — otherwise unreviewed code
    # rides in under a green code_review_passed (PR#79 finding #2).
    declared = list(cs.scope.get("files", [])) if cs is not None else []
    if reviewer == "code-reviewer" and working_tree_dirty(root, declared):
        click.echo(
            format_error(
                subcommand=subcommand,
                message="in-scope files have uncommitted changes; cannot verify the reviewed diff",
                hint="Commit or discard the in-scope edits, then prepare and begin a fresh round.",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    _validate_reviewer_state_or_exit(cs, reviewer=reviewer, subcommand=subcommand)
    try:
        adapter = get_reviewer_protocol(
            run.protocol, executable=run.protocol
        )
        telemetry_path: Path | None = None
        if isinstance(run.invocation, dict):
            frozen_telemetry_path = run.invocation.get("telemetry_path")
            if isinstance(frozen_telemetry_path, str) and frozen_telemetry_path:
                telemetry_path = Path(frozen_telemetry_path)
        parsed = adapter.parse_result(raw_path, telemetry_path=telemetry_path)
        verdict = validate_verdict_mapping(parsed.verdict)
    except (ReviewerProtocolError, VerdictError, ValueError) as exc:
        click.echo(
            format_error(
                subcommand=subcommand,
                message=f"invalid reviewer result: {exc}",
                hint="Record an execution failure or correct the external result file.",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)

    expected = {
        "run_id": run_id,
        "source": run.source,
        "target_head": round_state.target_head,
        "contract_digest": round_state.contract_digest,
        "bundle_digest": round_state.bundle_digest,
    }
    contradictions = [
        key for key, value in expected.items() if verdict.get(key) != value
    ]
    if contradictions:
        click.echo(
            format_error(
                subcommand=subcommand,
                message="reviewer result binding mismatch: " + ", ".join(contradictions),
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    checklist = [
        entry.get("item")
        for entry in verdict["checklist"]
        if isinstance(entry, dict)
    ]
    required_checklist = list(round_state.checklist)
    if (
        len(checklist) != len(required_checklist)
        or set(checklist) != set(required_checklist)
    ):
        click.echo(
            format_error(
                subcommand=subcommand,
                message="reviewer result must cover every frozen checklist item exactly once",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    missing_prior = check_disposed(
        verdict, list(round_state.open_finding_ids)
    )
    if missing_prior:
        click.echo(
            format_error(
                subcommand=subcommand,
                message="reviewer result did not dispose prior finding(s): "
                + ", ".join(missing_prior),
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    if verdict.get("scope_sufficient") is False and not verdict.get("findings"):
        click.echo(
            format_error(
                subcommand=subcommand,
                message="scope-insufficient result requires a finding explaining the gap",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    if parsed.actual_model is not None and _model_contradicts(
        run.requested_model, parsed.actual_model
    ):
        click.echo(
            format_error(
                subcommand=subcommand,
                message=(
                    "producer-reported model contradicts the frozen request: "
                    f"{parsed.actual_model!r} != {run.requested_model!r}"
                ),
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)

    namespace = f"{run.source}/{run_id}/"
    normalized = dict(verdict)
    normalized_findings: list[dict[str, object]] = []
    for raw_finding in verdict.get("findings", []):
        if not isinstance(raw_finding, dict):
            continue
        finding = dict(raw_finding)
        finding_id = str(finding["id"])
        if not finding_id.startswith(namespace):
            finding["id"] = namespace + finding_id
        finding["source"] = run.source
        finding["run_id"] = run_id
        normalized_findings.append(finding)
    normalized["findings"] = normalized_findings
    receipt_id = _new_review_id("receipt")
    receipt: dict[str, object] = {
        "receipt_id": receipt_id,
        "run_id": run_id,
        "source": run.source,
        "protocol": run.protocol,
        "target_head": round_state.target_head,
        "contract_digest": round_state.contract_digest,
        "profile_digest": round_state.profile_digest,
        "requested_model": run.requested_model,
        "requested_options": run.requested_options,
        "actual_model": parsed.actual_model,
        "session_id": parsed.session_id,
        "usage": parsed.usage,
        "duration_ms": parsed.duration_ms,
        "tool_trace": parsed.tool_trace,
    }
    framework = cs.framework if cs is not None else "plain"
    _emit_review_event(
        root,
        change=change,
        reviewer=reviewer,
        event_type="review_result_imported",
        reason="caller-owned external reviewer result imported",
        actor=Actor(type="agent", identifier=run.source),
        framework=framework,
        payload={
            "epoch_id": execution.epoch_id,
            "round_id": round_state.round_id,
            "run_id": run_id,
            "source": run.source,
            "contract_digest": round_state.contract_digest,
            "target_head": round_state.target_head,
            "result_digest": result_digest,
            "verdict": normalized,
            "receipt": receipt,
        },
        subcommand=subcommand,
    )
    outcome, milestone = _close_round_if_terminal(
        root,
        change=change,
        reviewer=reviewer,
        round_id=round_state.round_id,
        framework=framework,
    )
    refresh_state_after_emit(root)
    new_cs = derive_state(events_path(root)).get(change)
    data = {
        "change": change,
        "reviewer": reviewer,
        "run_id": run_id,
        "receipt_id": receipt_id,
        "result_digest": result_digest,
        "actual_model": parsed.actual_model,
        "session_id": parsed.session_id,
        "usage_available": parsed.usage is not None,
        "round_outcome": outcome,
        "milestone": milestone,
        "new_state": new_cs.current_state if new_cs is not None else None,
    }
    if ctx.obj.get("json"):
        click.echo(
            json_envelope(
                command=subcommand,
                status="pass",
                exit_code=EXIT_OK,
                data=data,
            )
        )
    elif not ctx.obj.get("quiet"):
        click.echo(
            f"super-harness: imported {run_id} ({run.source}); "
            f"round={outcome or 'open'}"
        )
    sys.exit(EXIT_OK)


@review_group.group("run")
def run_group() -> None:
    """Record failures for caller-owned reviewer runs."""


@run_group.command("fail")
@click.argument("change")
@_reviewer_opt
@click.option("--run-id", required=True)
@click.option("--reason", required=True)
@click.pass_context
def fail_run(
    ctx: click.Context, change: str, reviewer: str, run_id: str, reason: str
) -> None:
    """Record an external producer failure without retrying it."""

    subcommand = "review run fail"
    try:
        root = find_harness_root(Path(ctx.obj.get("workspace") or "."))
    except HarnessNotInitialized as exc:
        click.echo(
            format_error(subcommand=subcommand, message=exc.message, hint=exc.hint),
            err=True,
        )
        sys.exit(EXIT_NO_CONFIG)
    cs = derive_state(events_path(root)).get(change)
    _validate_reviewer_state_or_exit(cs, reviewer=reviewer, subcommand=subcommand)
    execution = derive_review_execution(_change_events(root, change), reviewer)
    round_state, run = _find_review_run_or_exit(execution, run_id, subcommand)
    status = run.status
    if status == "imported":
        click.echo(
            format_error(
                subcommand=subcommand,
                message="cannot fail a run after its result was imported",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    if status == "failed":
        if run.failure_reason == reason:
            if not ctx.obj.get("quiet"):
                click.echo(f"super-harness: run {run_id} failure already recorded")
            sys.exit(EXIT_OK)
        click.echo(
            format_error(
                subcommand=subcommand,
                message="conflicting second failure reason for this run",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    framework = cs.framework if cs is not None else "plain"
    _emit_review_event(
        root,
        change=change,
        reviewer=reviewer,
        event_type="review_run_failed",
        reason=reason,
        actor=Actor(type="agent", identifier=resolve_identity(root, None)),
        framework=framework,
        payload={
            "epoch_id": execution.epoch_id,
            "round_id": round_state.round_id,
            "run_id": run_id,
            "source": run.source,
            "contract_digest": round_state.contract_digest,
            "target_head": round_state.target_head,
        },
        subcommand=subcommand,
    )
    outcome, milestone = _close_round_if_terminal(
        root,
        change=change,
        reviewer=reviewer,
        round_id=round_state.round_id,
        framework=framework,
    )
    refresh_state_after_emit(root)
    data = {
        "change": change,
        "reviewer": reviewer,
        "run_id": run_id,
        "round_outcome": outcome,
        "milestone": milestone,
    }
    if ctx.obj.get("json"):
        click.echo(
            json_envelope(
                command=subcommand,
                status="pass",
                exit_code=EXIT_OK,
                data=data,
            )
        )
    elif not ctx.obj.get("quiet"):
        click.echo(
            f"super-harness: recorded failed run {run_id}; round={outcome or 'open'}"
        )
    sys.exit(EXIT_OK)


def _human_source_or_exit(
    governance: ReviewGovernance, reviewer: str, source: str | None, subcommand: str
) -> str:
    candidates = [
        name
        for name, configured in governance.sources.items()
        if configured.kind == "human"
    ]
    if source is not None:
        if source not in candidates:
            click.echo(
                format_error(
                    subcommand=subcommand,
                    message=f"source {source!r} is not a configured human source",
                    hint=f"Human sources: {', '.join(candidates) or '(none)' }.",
                ),
                err=True,
            )
            sys.exit(EXIT_VALIDATION)
        return source
    if len(candidates) != 1:
        click.echo(
            format_error(
                subcommand=subcommand,
                message="human review needs exactly one selected human source",
                hint=(
                    "Configure one human source or pass --source. Candidates: "
                    + (", ".join(candidates) or "(none)")
                ),
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    return candidates[0]


def _human_draft_dir(root: Path, change: str, reviewer: str) -> Path:
    return _reviewer_dir(root, change, reviewer) / "human-drafts"


@review_group.group("human")
def human_group() -> None:
    """Inspect and explicitly confirm first-class human review receipts."""


@human_group.command("inspect")
@click.argument("change")
@_reviewer_opt
@click.option("--pager", is_flag=True, help="Render the review packet through a pager.")
@click.pass_context
def human_inspect(
    ctx: click.Context, change: str, reviewer: str, pager: bool
) -> None:
    """Show compact packet metadata or page its human-readable inspection contract."""

    subcommand = "review human inspect"
    try:
        root = find_harness_root(Path(ctx.obj.get("workspace") or "."))
    except HarnessNotInitialized as exc:
        click.echo(
            format_error(subcommand=subcommand, message=exc.message, hint=exc.hint),
            err=True,
        )
        sys.exit(EXIT_NO_CONFIG)
    cs = derive_state(events_path(root)).get(change)
    _validate_reviewer_state_or_exit(cs, reviewer=reviewer, subcommand=subcommand)
    packet = _read_packet_or_exit(root, change, reviewer, subcommand)
    packet_path = _draft_packet_path(root, change, reviewer)
    assignments = [
        assignment
        for assignment in packet.get("assignments", [])
        if isinstance(assignment, dict)
    ]
    data = {
        "change": change,
        "reviewer": reviewer,
        "packet_path": str(packet_path),
        "target_head": packet.get("target_head"),
        "contract_digest": packet.get("contract_digest"),
        "bundle_digest": packet.get("bundle_digest"),
        "source_count": len(assignments),
        "checklist_count": len(packet.get("checklist", [])),
        "warnings": packet.get("warnings", []),
    }
    if pager:
        if not _interactive_terminal():
            click.echo(
                format_error(
                    subcommand=subcommand,
                    message="--pager requires an interactive human-owned TTY",
                    hint="Run without --pager for compact metadata.",
                ),
                err=True,
            )
            sys.exit(EXIT_VALIDATION)
        lines = [
            f"Review: {change} / {reviewer}",
            f"Target HEAD: {packet.get('target_head')}",
            f"Contract digest: {packet.get('contract_digest')}",
            "",
            "Checklist:",
            *[f"- {item}" for item in packet.get("checklist", [])],
        ]
        for assignment in assignments:
            inspection = assignment.get("inspection")
            if not isinstance(inspection, dict):
                continue
            lines.extend(
                [
                    "",
                    f"Source: {assignment.get('source')}",
                    f"Range: {inspection.get('base')}..{inspection.get('head')}",
                    "Files:",
                    *[f"- {item}" for item in inspection.get("files", [])],
                    "Git argv:",
                    json.dumps(inspection.get("diff_argv", []), ensure_ascii=False),
                ]
            )
        click.echo_via_pager("\n".join(lines) + "\n")
    elif ctx.obj.get("json"):
        click.echo(
            json_envelope(
                command=subcommand,
                status="pass",
                exit_code=EXIT_OK,
                data=data,
            )
        )
    elif not ctx.obj.get("quiet"):
        click.echo(
            f"super-harness: human review packet {packet_path} "
            f"(target={str(packet.get('target_head'))[:12]}, "
            f"checklist={data['checklist_count']})"
        )
    sys.exit(EXIT_OK)


@human_group.command("draft")
@click.argument("change")
@_reviewer_opt
@click.option("--source", default=None)
@click.option(
    "--verdict-file",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
)
@click.pass_context
def human_draft(
    ctx: click.Context,
    change: str,
    reviewer: str,
    source: str | None,
    verdict_file: str,
) -> None:
    """Validate a human verdict and create a short-lived confirmation nonce."""

    subcommand = "review human draft"
    try:
        root = find_harness_root(Path(ctx.obj.get("workspace") or "."))
    except HarnessNotInitialized as exc:
        click.echo(
            format_error(subcommand=subcommand, message=exc.message, hint=exc.hint),
            err=True,
        )
        sys.exit(EXIT_NO_CONFIG)
    cs = derive_state(events_path(root)).get(change)
    _validate_reviewer_state_or_exit(cs, reviewer=reviewer, subcommand=subcommand)
    governance = _load_governance_or_exit(root, subcommand)
    human_source = _human_source_or_exit(governance, reviewer, source, subcommand)
    packet = _read_packet_or_exit(root, change, reviewer, subcommand)
    try:
        current_head = resolve_commit(root)
        verdict = parse_verdict_file(Path(verdict_file))
        raw_bytes = Path(verdict_file).read_bytes()
    except (GitScopeError, OSError, VerdictError) as exc:
        click.echo(
            format_error(subcommand=subcommand, message=f"invalid human verdict: {exc}"),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    if current_head != packet.get("target_head"):
        click.echo(
            format_error(
                subcommand=subcommand,
                message="prepared human review packet is stale",
                hint="Commit the intended target and run review prepare again.",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    for key in ("target_head", "contract_digest"):
        if key in verdict and verdict[key] != packet.get(key):
            click.echo(
                format_error(
                    subcommand=subcommand,
                    message=f"human verdict {key} contradicts the prepared packet",
                ),
                err=True,
            )
            sys.exit(EXIT_VALIDATION)
    if verdict["bundle_digest"] != packet.get("bundle_digest"):
        click.echo(
            format_error(
                subcommand=subcommand,
                message="human verdict bundle_digest is stale",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    checklist = [
        entry.get("item")
        for entry in verdict["checklist"]
        if isinstance(entry, dict)
    ]
    required = list(packet.get("checklist", []))
    if len(checklist) != len(required) or set(checklist) != set(required):
        click.echo(
            format_error(
                subcommand=subcommand,
                message="human verdict must cover every checklist item exactly once",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    missing_prior = check_disposed(
        verdict, derive_open_findings(_change_events(root, change), change)
    )
    if missing_prior:
        click.echo(
            format_error(
                subcommand=subcommand,
                message="human verdict did not dispose prior finding(s): "
                + ", ".join(missing_prior),
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    nonce = _new_review_id("nonce")
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=15)
    draft_dir = _human_draft_dir(root, change, reviewer)
    draft_dir.mkdir(parents=True, exist_ok=True)
    draft_path = draft_dir / f"{nonce}.json"
    draft_path.write_text(
        json.dumps(
            {
                "nonce": nonce,
                "change": change,
                "reviewer": reviewer,
                "source": human_source,
                "target_head": packet["target_head"],
                "contract_digest": packet["contract_digest"],
                "bundle_digest": packet["bundle_digest"],
                "profile_digest": packet["profile_digest"],
                "verdict_digest": sha256(raw_bytes).hexdigest(),
                "verdict": verdict,
                "expires_at": expires_at.isoformat(),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    data = {
        "change": change,
        "reviewer": reviewer,
        "source": human_source,
        "nonce": nonce,
        "expires_at": expires_at.isoformat(),
        "target_head": packet["target_head"],
        "contract_digest": packet["contract_digest"],
    }
    if ctx.obj.get("json"):
        click.echo(
            json_envelope(
                command=subcommand,
                status="pass",
                exit_code=EXIT_OK,
                data=data,
            )
        )
    elif not ctx.obj.get("quiet"):
        click.echo(
            f"super-harness: human verdict draft ready; nonce={nonce} "
            f"expires={expires_at.isoformat()}"
        )
    sys.exit(EXIT_OK)


@human_group.command("confirm")
@click.argument("change")
@_reviewer_opt
@click.option("--nonce", required=True)
@click.pass_context
def human_confirm(
    ctx: click.Context, change: str, reviewer: str, nonce: str
) -> None:
    """Confirm a nonce-bound human verdict in a human-owned interactive TTY."""

    subcommand = "review human confirm"
    if not _interactive_terminal():
        click.echo(
            format_error(
                subcommand=subcommand,
                message="human review confirmation requires an interactive TTY",
                hint="A code agent must not self-confirm; ask the human to run this command.",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    try:
        root = find_harness_root(Path(ctx.obj.get("workspace") or "."))
    except HarnessNotInitialized as exc:
        click.echo(
            format_error(subcommand=subcommand, message=exc.message, hint=exc.hint),
            err=True,
        )
        sys.exit(EXIT_NO_CONFIG)
    prior_events = _change_events(root, change)
    if any(
        event.type == "review_result_imported"
        and isinstance(event.payload.get("receipt"), dict)
        and event.payload["receipt"].get("human_nonce") == nonce
        for event in prior_events
    ):
        click.echo(f"super-harness: human nonce {nonce} already confirmed")
        sys.exit(EXIT_OK)
    cs = derive_state(events_path(root)).get(change)
    _validate_reviewer_state_or_exit(cs, reviewer=reviewer, subcommand=subcommand)
    # Load governance up front, before any event is emitted. Loading it late (in
    # the middle of the round_started/result_imported/round_closed sequence) let a
    # malformed/removed governance file or missing role abort with a raw traceback
    # after three events were written but before the milestone — and the nonce
    # idempotency check above then reported a false "already confirmed" on retry
    # while the change stayed stuck in AWAITING_*_REVIEW (PR#79 finding #10).
    governance = _load_governance_or_exit(root, subcommand)
    if reviewer not in governance.roles:
        click.echo(
            format_error(
                subcommand=subcommand,
                message=f"review role {reviewer!r} is not configured",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    role = governance.roles[reviewer]
    draft_path = _human_draft_dir(root, change, reviewer) / f"{nonce}.json"
    try:
        draft: object = json.loads(draft_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        click.echo(
            format_error(
                subcommand=subcommand,
                message=f"human review draft is unavailable: {exc}",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    if not isinstance(draft, dict) or draft.get("nonce") != nonce:
        click.echo(
            format_error(subcommand=subcommand, message="human review nonce is invalid"),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    try:
        expires_at = datetime.fromisoformat(str(draft["expires_at"]))
    except (KeyError, ValueError):
        click.echo(
            format_error(subcommand=subcommand, message="human review nonce expiry is invalid"),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    if datetime.now(timezone.utc) >= expires_at:
        click.echo(
            format_error(
                subcommand=subcommand,
                message="human review nonce expired",
                hint="Run review human draft again after re-inspecting the packet.",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    packet = _read_packet_or_exit(root, change, reviewer, subcommand)
    if any(
        draft.get(key) != packet.get(key)
        for key in ("target_head", "contract_digest", "bundle_digest", "profile_digest")
    ) or resolve_commit(root) != draft.get("target_head"):
        click.echo(
            format_error(
                subcommand=subcommand,
                message="human review nonce is stale for the current packet or HEAD",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    # Same reviewed-diff guard as automated import: refuse a human code-review
    # confirmation while in-scope files carry uncommitted changes the human's
    # verdict never covered (PR#79 finding #2).
    declared = list(cs.scope.get("files", [])) if cs is not None else []
    if reviewer == "code-reviewer" and working_tree_dirty(root, declared):
        click.echo(
            format_error(
                subcommand=subcommand,
                message="in-scope files have uncommitted changes; cannot verify the reviewed diff",
                hint="Commit or discard the in-scope edits, then re-inspect and re-draft.",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    if not click.confirm(
        f"Confirm human {reviewer} verdict for {change} at "
        f"{str(draft['target_head'])[:12]}?",
        default=False,
    ):
        click.echo("super-harness: human review confirmation cancelled")
        sys.exit(EXIT_VALIDATION)
    verdict = draft.get("verdict")
    if not isinstance(verdict, dict):
        click.echo(
            format_error(subcommand=subcommand, message="human review verdict is invalid"),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    source = str(draft["source"])
    execution = derive_review_execution(prior_events, reviewer)
    if execution.epoch_id is None:
        click.echo(
            format_error(subcommand=subcommand, message="no active review epoch"),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    round_id = _new_review_id("round")
    run_id = _new_review_id("run")
    namespace = f"{source}/{run_id}/"
    normalized = dict(verdict)
    normalized_findings: list[dict[str, object]] = []
    for raw_finding in verdict.get("findings", []):
        if not isinstance(raw_finding, dict):
            continue
        finding = dict(raw_finding)
        finding_id = str(finding["id"])
        if not finding_id.startswith(namespace):
            finding["id"] = namespace + finding_id
        finding["source"] = source
        finding["run_id"] = run_id
        normalized_findings.append(finding)
    normalized["findings"] = normalized_findings
    outcome = (
        "rejected"
        if normalized.get("scope_sufficient") is False or failing_items(normalized)
        else "approved"
    )
    # B-layer dead documentation-reference gate. The automated close path
    # (`_close_round_if_terminal`) refuses a code-review approval when a doc still
    # references a code symbol that no longer resolves; the human confirmation
    # path must apply the same gate or a human-only code review (init's default
    # governance) would merge a high-confidence dead doc reference (PR#79 finding
    # #5).
    if reviewer == "code-reviewer" and outcome == "approved":
        dead_refs = [
            finding
            for finding in scan_doc_refs(root).findings
            if finding.confidence == "high"
        ]
        if dead_refs:
            outcome = "rejected"
            normalized["findings"] = [
                *normalized_findings,
                *(
                    {
                        "id": f"harness/doc-ref/{index}",
                        "severity": "major",
                        "file": finding.doc_file,
                        "line": finding.line,
                        "summary": (
                            f"Documented code symbol no longer resolves: {finding.symbol}"
                        ),
                        "source": "harness",
                    }
                    for index, finding in enumerate(dead_refs, start=1)
                ),
            ]
    framework = cs.framework if cs is not None else "plain"
    actor = Actor(type="human", identifier=resolve_identity(root, None))
    run_payload: dict[str, object] = {
        "run_id": run_id,
        "source": source,
        "protocol": "human",
        "requested_model": "human",
        "requested_options": {},
        "cost_class": None,
        "prompt_digest": None,
    }
    _emit_review_event(
        root,
        change=change,
        reviewer=reviewer,
        event_type="review_round_started",
        reason="human review confirmation started",
        actor=actor,
        framework=framework,
        payload={
            "epoch_id": execution.epoch_id,
            "round_id": round_id,
            "contract_digest": draft["contract_digest"],
            "bundle_digest": draft["bundle_digest"],
            "target_head": draft["target_head"],
            "profile_digest": draft["profile_digest"],
            "automatic": False,
            "checklist": list(packet["checklist"]),
            # Code-review open findings only; a human plan-reviewer is never shown
            # them either (PR#79 finding #3).
            "open_finding_ids": (
                derive_open_findings(prior_events, change)
                if reviewer == "code-reviewer"
                else []
            ),
            "runs": [run_payload],
        },
        subcommand=subcommand,
    )
    receipt_id = _new_review_id("receipt")
    receipt = {
        "receipt_id": receipt_id,
        "human_nonce": nonce,
        "verdict_digest": draft["verdict_digest"],
        "run_id": run_id,
        "source": source,
        "target_head": draft["target_head"],
        "contract_digest": draft["contract_digest"],
        "profile_digest": draft["profile_digest"],
        "requested_model": "human",
        "requested_options": {},
        "actual_model": None,
        "usage": None,
        "duration_ms": None,
        "tool_trace": None,
    }
    _emit_review_event(
        root,
        change=change,
        reviewer=reviewer,
        event_type="review_result_imported",
        reason="human-confirmed verdict imported",
        actor=actor,
        framework=framework,
        payload={
            "epoch_id": execution.epoch_id,
            "round_id": round_id,
            "run_id": run_id,
            "source": source,
            "contract_digest": draft["contract_digest"],
            "target_head": draft["target_head"],
            "result_digest": draft["verdict_digest"],
            "verdict": normalized,
            "receipt": receipt,
        },
        subcommand=subcommand,
    )
    _emit_review_event(
        root,
        change=change,
        reviewer=reviewer,
        event_type="review_round_closed",
        reason=f"human-confirmed review {outcome}",
        actor=actor,
        framework=framework,
        payload={
            "epoch_id": execution.epoch_id,
            "round_id": round_id,
            "contract_digest": draft["contract_digest"],
            "target_head": draft["target_head"],
            "profile_digest": draft["profile_digest"],
            "outcome": outcome,
        },
        subcommand=subcommand,
    )
    # Count every independent source that has imported for this exact contract:
    # the automated receipts already on record plus this human receipt. A human
    # approval alone must not satisfy a role that requires more independent
    # sources; the human is the completer that closes the whole participant set
    # (PR#79 finding #1). For human-only governance (min_independent == 1) this is
    # satisfied by the single human source.
    prior_imported = {
        prior_source
        for prior_round in execution.rounds
        if prior_round.contract_digest == draft["contract_digest"]
        and prior_round.target_head == draft["target_head"]
        for prior_source, prior_run in prior_round.runs.items()
        if prior_run.status == "imported"
    }
    independent_sources = sorted(prior_imported | {source})
    if outcome == "approved" and len(independent_sources) < role.min_independent:
        # The human receipt is durably recorded and the round is closed, but
        # governance is not yet satisfied: hold the approval milestone so the
        # change stays in its awaiting state (fail closed) until the remaining
        # independent sources import and a human re-draft completes the set.
        refresh_state_after_emit(root)
        remaining = role.min_independent - len(independent_sources)
        if not ctx.obj.get("quiet"):
            click.echo(
                f"super-harness: recorded human review; governance still needs "
                f"{remaining} more independent source(s) before approval "
                f"(have {len(independent_sources)}/{role.min_independent})"
            )
        sys.exit(EXIT_OK)
    milestone = _REVIEWER_PASS[reviewer] if outcome == "approved" else _REVIEWER_FAIL[reviewer]
    _emit_review_event(
        root,
        change=change,
        reviewer=reviewer,
        event_type=milestone,
        reason=f"explicit human review {outcome}",
        actor=actor,
        framework=framework,
        payload={
            "outcome": outcome,
            "reviewed_head": draft["target_head"],
            "contract_digest": draft["contract_digest"],
            "profile_digest": draft["profile_digest"],
            "independent_sources": independent_sources,
            "min_independent": role.min_independent,
            "receipt_ids": [receipt_id],
            "human_resolution": True,
            "verdict": normalized,
        },
        subcommand=subcommand,
    )
    refresh_state_after_emit(root)
    if not ctx.obj.get("quiet"):
        new_cs = derive_state(events_path(root)).get(change)
        click.echo(
            f"super-harness: confirmed human review ({outcome}) → "
            f"{new_cs.current_state if new_cs is not None else None}"
        )
    sys.exit(EXIT_OK)
