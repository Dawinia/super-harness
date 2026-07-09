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

import sys
from dataclasses import asdict
from pathlib import Path
from typing import TypedDict

import click

from super_harness.cli.errors import format_error
from super_harness.cli.output import json_envelope
from super_harness.core.active_change import pick_active_change
from super_harness.core.paths import (
    HarnessNotInitialized,
    events_path,
    find_harness_root,
)
from super_harness.core.reducer import derive_state
from super_harness.core.review_verdict import read_change_events
from super_harness.engineering.reviewer_policy import (
    REVIEW_STATE_REVIEWER,
    ReviewerIndependencePolicy,
    ReviewerPolicyError,
    approved_review_sources,
    load_reviewer_policy,
)
from super_harness.exit_codes import EXIT_NO_CONFIG, EXIT_OK, EXIT_VALIDATION
from super_harness.gates.decisions import SUGGESTIONS


class _ReviewProgress(TypedDict):
    reviewer: str
    min_independent: int
    accepted_sources: list[str]
    missing_independent: int
    remaining_sources: list[str]
    instructions: dict[str, str]


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
    # HG-02.C: when a change sits in a review state, surface the configured
    # reviewer strategy so the agent/human knows whether to dispatch a Task
    # subagent or hand the review off to a person. Read-only; a malformed
    # reviewers policy surfaces as a config error (exit 2).
    def _reviewer_info(cs: object) -> tuple[str | None, ReviewerIndependencePolicy | None]:
        reviewer = REVIEW_STATE_REVIEWER.get(cs.current_state)  # type: ignore[attr-defined]
        if reviewer is None:
            return None, None
        return reviewer, load_reviewer_policy(root, reviewer)

    def _review_progress(change_id: str, reviewer: str, policy: ReviewerIndependencePolicy
                         ) -> _ReviewProgress:
        events = read_change_events(events_path(root), change_id)
        accepted = sorted(approved_review_sources(events, reviewer))
        remaining = [s for s in policy.allowed_sources if s not in accepted]
        missing = max(policy.min_independent - len(accepted), 0)
        return {
            "reviewer": reviewer,
            "min_independent": policy.min_independent,
            "accepted_sources": accepted,
            "missing_independent": missing,
            "remaining_sources": remaining,
            "instructions": {
                source: policy.source_instructions[source]
                for source in remaining
                if source in policy.source_instructions
            },
        }

    try:
        if ctx.obj.get("json"):
            changes_data = []
            for cs in target:
                entry = asdict(cs)
                entry["next"] = SUGGESTIONS.get(cs.current_state)
                reviewer, policy = _reviewer_info(cs)
                if reviewer is not None:
                    assert policy is not None
                    entry["reviewer"] = reviewer
                    entry["reviewer_strategy"] = policy.strategy
                    entry["review_progress"] = _review_progress(
                        cs.change_id, reviewer, policy
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
                reviewer, policy = _reviewer_info(cs)
                if reviewer is not None:
                    assert policy is not None
                    click.echo(f"  reviewer: {reviewer} (strategy: {policy.strategy})")
                    progress = _review_progress(cs.change_id, reviewer, policy)
                    accepted = progress["accepted_sources"]
                    remaining = progress["remaining_sources"]
                    click.echo(
                        "  review progress: "
                        f"{len(accepted)}/{progress['min_independent']} independent source(s)"
                    )
                    if accepted:
                        click.echo(f"    accepted: {', '.join(accepted)}")
                    if remaining:
                        click.echo(f"    remaining: {', '.join(remaining)}")
                    instructions = progress["instructions"]
                    for source, text in instructions.items():
                        click.echo(f"    {source}: {text}")
                nxt = SUGGESTIONS.get(cs.current_state)
                if nxt:
                    click.echo(f"  next: {nxt}")
    except ReviewerPolicyError as e:
        click.echo(format_error(subcommand="status", message=str(e)), err=True)
        sys.exit(EXIT_VALIDATION)
    sys.exit(EXIT_OK)
