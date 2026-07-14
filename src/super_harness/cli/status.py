"""`status` command — read-only view of per-change current state.

Per cli-command-surface §3.5. Three call shapes:
- `status <slug>`     → render that one change (empty result if slug unknown).
- `status --all`      → render every change, including terminal states.
- `status` (no args)  → fall back to the MOST RECENTLY active change.

The no-args fallback resolves the most recently active non-terminal change (by
`last_event_at`), via the shared `core.active_change.pick_active_change` — the
same definition the gate/resume/done use, so they never drift. (It was formerly
"first/oldest active", a v0.1 placeholder that let a stale merged-but-not-archived
change hijack the resolution — HG-STALE-MERGED-CHANGE.) NOT git-branch parsing.

Because this command never emits, it does NOT call `post_emit_refresh` — that
helper exists solely to keep state.yaml current after a write. Reads always
recompute from events.jsonl (same trade-off as `change list`: freshness over
O(1) state.yaml lookup; Phase 8 daemon hot path will read state.yaml instead).
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, TypedDict

import click

from super_harness.cli.errors import format_error
from super_harness.cli.output import json_envelope
from super_harness.core.active_change import pick_active_change
from super_harness.core.paths import (
    HarnessNotInitialized,
    events_path,
    find_harness_root,
    pending_reviews_dir,
)
from super_harness.core.reducer import derive_state
from super_harness.core.review_verdict import read_change_events
from super_harness.core.scope_match import GitScopeError, resolve_commit
from super_harness.engineering.review_governance import (
    ReviewGovernance,
    ReviewGovernanceError,
    load_review_governance,
)
from super_harness.engineering.review_profiles import (
    ReviewProfilesError,
    load_review_profiles,
)
from super_harness.engineering.review_runs import derive_review_execution
from super_harness.exit_codes import EXIT_NO_CONFIG, EXIT_OK, EXIT_VALIDATION
from super_harness.gates.decisions import SUGGESTIONS


class _ReviewProgress(TypedDict):
    reviewer: str
    min_independent: int
    required_sources: list[str]
    imported_sources: list[str]
    pending_sources: list[str]
    failed_sources: list[str]
    retained_sources: list[str]
    stale_sources: list[str]
    automatic_rounds_used: int
    automatic_rounds_remaining: int
    available_authorizations: list[str]
    packet: dict[str, object] | None
    source_profiles: dict[str, dict[str, object]]
    next_command: str


REVIEW_STATE_REVIEWER: dict[str, str] = {
    "AWAITING_PLAN_REVIEW": "plan-reviewer",
    "AWAITING_CODE_REVIEW": "code-reviewer",
    "CODE_REVIEW_REJECTED": "code-reviewer",
}


def _format_agent_option_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True)
    return str(value)


def _format_agent_options(options: dict[str, object]) -> str:
    return ", ".join(
        f"{key}={_format_agent_option_value(value)}"
        for key, value in sorted(options.items())
    )


@click.command("status")
@click.argument("slug", required=False)
@click.option(
    "--all",
    "all_changes",
    is_flag=True,
    help="Show every change, including ARCHIVED + ABANDONED.",
)
@click.pass_context
def status_cmd(ctx: click.Context, slug: str | None, all_changes: bool) -> None:
    """Show current state for one change, all changes, or the most recently active change."""
    # Mutex: `status <slug> --all` is incoherent — `--all` is a list-everything
    # flag, `<slug>` is a single-object selector. Reject at parse time with an
    # actionable error rather than silently letting one shadow the other.
    # Symmetric with `change list --active --archived` (same exit 2 + Hint).
    if all_changes and slug:
        click.echo(
            format_error(
                subcommand="status",
                message="--all cannot be combined with a slug argument",
                hint=(
                    "Use `status <slug>` to query one change OR "
                    "`status --all` to list all."
                ),
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    try:
        root = find_harness_root(Path(ctx.obj.get("workspace") or "."))
    except HarnessNotInitialized as e:
        # Route remediation to format_error's `Hint:` line per the format
        # contract (message stays one-line; remediation is a separate field).
        click.echo(
            format_error(subcommand="status", message=e.message, hint=e.hint),
            err=True,
        )
        sys.exit(EXIT_NO_CONFIG)
    derived = derive_state(events_path(root))
    if all_changes:
        target = list(derived.values())
    elif slug:
        # Identifier semantics: a specific slug is an object identifier, NOT a
        # filter. When the user names a specific object that doesn't exist, exit
        # with EXIT_VALIDATION + actionable error — matches `change resume
        # <unknown>` (this CLI), `git show <bad-sha>`, `docker inspect <missing>`,
        # `kubectl get pod <missing>`, `gh pr view <missing>`. Filter commands
        # (`change list`) returning empty stays exit 0.
        if slug not in derived:
            click.echo(
                format_error(
                    subcommand="status",
                    message=f"unknown change slug: {slug!r}",
                    hint="Run `super-harness change list` to see known changes.",
                ),
                err=True,
            )
            sys.exit(EXIT_VALIDATION)
        target = [derived[slug]]
    else:
        # No args → the most recently active non-terminal change, via the shared
        # resolver (same definition gate/resume/done use — never drifts).
        active_id = pick_active_change(
            (cid, cs.current_state, cs.last_event_at) for cid, cs in derived.items()
        )
        target = [derived[active_id]] if active_id else []
    def _reviewer_info(cs: Any) -> tuple[str | None, ReviewGovernance | None]:
        reviewer = REVIEW_STATE_REVIEWER.get(cs.current_state)
        if reviewer is None:
            return None, None
        governance = load_review_governance(root)
        if reviewer not in governance.roles:
            raise ReviewGovernanceError(f"review role {reviewer!r} is not configured")
        return reviewer, governance

    def _review_progress(
        change_id: str, reviewer: str, governance: ReviewGovernance
    ) -> _ReviewProgress:
        events = read_change_events(events_path(root), change_id)
        execution = derive_review_execution(events, reviewer)
        role = governance.roles[reviewer]
        packet_path = (
            pending_reviews_dir(root, change_id) / reviewer / "draft.packet.json"
        )
        packet: dict[str, Any] | None = None
        try:
            raw_packet: object = json.loads(packet_path.read_text(encoding="utf-8"))
            if isinstance(raw_packet, dict):
                packet = raw_packet
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            pass
        contract_digest = packet.get("contract_digest") if packet is not None else None
        target_head = packet.get("target_head") if packet is not None else None
        profile_digest = packet.get("profile_digest") if packet is not None else None
        try:
            current_head: str | None = resolve_commit(root)
        except GitScopeError:
            current_head = None
        packet_stale = (
            packet is not None
            and current_head is not None
            and target_head != current_head
        )

        def matches_packet(round_state: Any) -> bool:
            return bool(
                packet is not None
                and round_state.contract_digest == contract_digest
                and round_state.target_head == target_head
                and round_state.profile_digest == profile_digest
            )

        latest: dict[str, Any] = {}
        stale: set[str] = set()
        for round_state in execution.rounds:
            matches = matches_packet(round_state)
            for source, run in round_state.runs.items():
                if matches:
                    latest[source] = run
                elif run.status == "imported":
                    stale.add(source)
        imported = sorted(
            source for source, run in latest.items() if run.status == "imported"
        )
        stale.difference_update(imported)
        current_round = execution.rounds[-1] if execution.rounds else None
        current_round_matches = bool(
            current_round is not None and matches_packet(current_round)
        )
        retained = (
            list(execution.retained_sources) if current_round_matches else []
        )
        if packet_stale:
            stale.update(imported)
            stale.update(retained)
            imported = []
            retained = []
        if current_round is not None and current_round_matches:
            pending = sorted(
                source
                for source, run in current_round.runs.items()
                if run.status == "pending"
            )
            failed = sorted(
                source
                for source, run in current_round.runs.items()
                if run.status == "failed"
            )
        else:
            pending = []
            failed = []
        if packet_stale:
            pending = []
            failed = []
        profiles = load_review_profiles(root)
        source_profiles: dict[str, dict[str, object]] = {}
        for source in role.participants:
            profile_payload: dict[str, object] = {
                "kind": governance.sources[source].kind,
            }
            if source in profiles.sources:
                profile_payload.update(
                    {
                        "protocol": profiles.sources[source].protocol,
                        "model": profiles.sources[source].model,
                        "cost_class": profiles.sources[source].cost_class,
                        "agent_options": profiles.sources[source].agent_options,
                    }
                )
            source_profiles[source] = profile_payload
        automated = [
            source
            for source in role.participants
            if governance.sources[source].kind == "automated"
        ]
        human_participants = [
            source
            for source in role.participants
            if governance.sources[source].kind == "human"
        ]
        human_flags = (
            f" --source {human_participants[0]}" if human_participants else ""
        )
        retry_sources = [source for source in automated if source not in imported]
        retry_flags = "".join(f" --source {source}" for source in retry_sources)
        remaining_rounds = max(
            role.max_automatic_rounds_per_epoch - execution.automatic_rounds_used, 0
        )
        if packet is None or packet_stale:
            next_command = (
                f"super-harness review prepare {change_id} --reviewer {reviewer}"
            )
        elif pending:
            next_command = (
                "super-harness review result import ... or review run fail ... "
                f"for pending source(s): {', '.join(pending)}"
            )
        elif not automated:
            next_command = (
                f"super-harness review human inspect {change_id} --reviewer {reviewer}"
                f"{human_flags} --pager"
            )
        elif remaining_rounds == 0 and not execution.available_authorization_ids:
            if human_participants:
                next_command = (
                    f"super-harness review human inspect {change_id} "
                    f"--reviewer {reviewer}{human_flags} --pager"
                )
            else:
                failed_retry = ", ".join(retry_sources) or "(none)"
                recovery_action = (
                    f"restore failed source(s): {failed_retry}"
                    if retry_sources and set(retry_sources).issubset(failed)
                    else f"collect required source(s): {failed_retry}"
                )
                next_command = (
                    f"{recovery_action}; then in a human-owned "
                    "TTY run super-harness review authorize "
                    f"{change_id} --reviewer {reviewer}{retry_flags} --reason <why>; "
                    f"then super-harness review begin {change_id} --reviewer {reviewer}"
                    f"{retry_flags}; otherwise human-only terminal decision: "
                    f"super-harness review skip {change_id} --reviewer {reviewer} "
                    "--override --reason <why>"
                )
        else:
            next_command = (
                f"super-harness review begin {change_id} --reviewer {reviewer}"
                f"{retry_flags}"
            )
        return {
            "reviewer": reviewer,
            "min_independent": role.min_independent,
            "required_sources": list(role.participants),
            "imported_sources": imported,
            "pending_sources": pending,
            "failed_sources": failed,
            "retained_sources": retained,
            "stale_sources": sorted(stale),
            "automatic_rounds_used": execution.automatic_rounds_used,
            "automatic_rounds_remaining": remaining_rounds,
            "available_authorizations": list(execution.available_authorization_ids),
            "packet": (
                {
                    "path": str(packet_path),
                    "contract_digest": contract_digest,
                    "target_head": target_head,
                    "current_head": current_head,
                    "stale": packet_stale,
                }
                if packet is not None
                else None
            ),
            "source_profiles": source_profiles,
            "next_command": next_command,
        }

    try:
        if ctx.obj.get("json"):
            changes_data = []
            for cs in target:
                entry = asdict(cs)
                entry["next"] = SUGGESTIONS.get(cs.current_state)
                reviewer, governance = _reviewer_info(cs)
                if reviewer is not None:
                    assert governance is not None
                    entry["reviewer"] = reviewer
                    entry["review_progress"] = _review_progress(
                        cs.change_id, reviewer, governance
                    )
                changes_data.append(entry)
            click.echo(
                json_envelope(
                    command="status", status="pass", exit_code=EXIT_OK,
                    data={"changes": changes_data},
                )
            )
        else:
            for cs in target:
                click.echo(f"{cs.change_id}: {cs.current_state}")
                click.echo(f"  last: {cs.last_event_type} @ {cs.last_event_at}")
                # `scope` is `dict[str, Any]` defaulting to {} — empty dict is
                # correctly falsy, so this skips changes that haven't reached
                # `plan_ready` yet (scope is populated from plan_ready payload).
                if cs.scope:
                    click.echo(f"  scope: {cs.scope}")
                reviewer, governance = _reviewer_info(cs)
                if reviewer is not None:
                    assert governance is not None
                    progress = _review_progress(cs.change_id, reviewer, governance)
                    imported = progress["imported_sources"]
                    required = progress["required_sources"]
                    click.echo(f"  reviewer: {reviewer} (execution protocol)")
                    click.echo(
                        "  review progress: "
                        f"{len(imported)}/{progress['min_independent']} imported source(s)"
                    )
                    click.echo(
                        "    automatic rounds: "
                        f"{progress['automatic_rounds_used']} used, "
                        f"{progress['automatic_rounds_remaining']} remaining"
                    )
                    packet = progress["packet"]
                    if isinstance(packet, dict):
                        packet_target = packet.get("target_head")
                        current_head = packet.get("current_head")
                        stale_suffix = "; stale" if packet.get("stale") else ""
                        click.echo(
                            f"    packet target: {packet_target} "
                            f"(current: {current_head or 'unknown'}{stale_suffix})"
                        )
                    if imported:
                        click.echo(f"    imported: {', '.join(imported)}")
                    remaining = [source for source in required if source not in imported]
                    if remaining:
                        click.echo(f"    remaining: {', '.join(remaining)}")
                    if progress["pending_sources"]:
                        click.echo(
                            f"    pending: {', '.join(progress['pending_sources'])}"
                        )
                    if progress["failed_sources"]:
                        click.echo(
                            f"    failed: {', '.join(progress['failed_sources'])}"
                        )
                    if progress["retained_sources"]:
                        click.echo(
                            f"    retained: {', '.join(progress['retained_sources'])}"
                        )
                    if progress["stale_sources"]:
                        click.echo(
                            f"    stale: {', '.join(progress['stale_sources'])}"
                        )
                    for source in required:
                        click.echo(f"    {source}:")
                        profile = progress["source_profiles"].get(source, {})
                        protocol = profile.get("protocol")
                        model = profile.get("model")
                        cost_class = profile.get("cost_class")
                        options = profile.get("agent_options")
                        if protocol:
                            click.echo(f"      protocol: {protocol}")
                        if model:
                            click.echo(f"      model: {model}")
                        if cost_class:
                            click.echo(f"      cost_class: {cost_class}")
                        if isinstance(options, dict) and options:
                            click.echo(f"      agent_options: {_format_agent_options(options)}")
                    click.echo(f"  review next: {progress['next_command']}")
                nxt = SUGGESTIONS.get(cs.current_state)
                if nxt:
                    click.echo(f"  next: {nxt}")
    except (ReviewGovernanceError, ReviewProfilesError) as e:
        click.echo(format_error(subcommand="status", message=str(e)), err=True)
        sys.exit(EXIT_VALIDATION)
    sys.exit(EXIT_OK)
