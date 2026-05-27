"""`status` command — read-only view of per-change current state.

Per cli-command-surface §3.5. Three call shapes:
- `status <slug>`     → render that one change (empty result if slug unknown).
- `status --all`      → render every change, including terminal states.
- `status` (no args)  → fall back to the first ACTIVE change (v0.1 simplification).

v0.1 fallback is deliberately dumb: "first active" by events.jsonl insertion
order, NOT git-branch parsing. The plan's "infer from git branch" wording is
aspirational — git-branch → slug correlation lands in a later phase. The
comment below keeps the simplification honest so future code-reviewers don't
mistake the placeholder for real branch dispatch.

Because this command never emits, it does NOT call `post_emit_refresh` — that
helper exists solely to keep state.yaml current after a write. Reads always
recompute from events.jsonl (same trade-off as `change list`: freshness over
O(1) state.yaml lookup; Phase 8 daemon hot path will read state.yaml instead).
"""
from __future__ import annotations

import sys
from dataclasses import asdict
from pathlib import Path

import click

from super_harness.cli.exit_codes import EXIT_NO_CONFIG, EXIT_OK
from super_harness.cli.output import json_envelope
from super_harness.core.paths import (
    HarnessNotInitialized,
    events_path,
    find_harness_root,
)
from super_harness.core.reducer import derive_state
from super_harness.core.state import TERMINAL_STATES


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
    """Show current state for one change, all changes, or the first active change."""
    try:
        root = find_harness_root(Path(ctx.obj.get("workspace") or "."))
    except HarnessNotInitialized as e:
        click.echo(str(e), err=True)
        sys.exit(EXIT_NO_CONFIG)
    derived = derive_state(events_path(root))
    if all_changes:
        target = list(derived.values())
    elif slug:
        # Unknown slug → empty result + exit 0. Mirrors `change list` semantics:
        # the query succeeded, the result happens to be empty. NOT exit 1.
        target = [derived[slug]] if slug in derived else []
    else:
        # v0.1 default: first ACTIVE change by events.jsonl insertion order.
        # TODO(post-v0.1): infer from current git branch when branch-naming
        # convention maps cleanly to slug. Today's "first active" is a deliberate
        # placeholder, not a bug.
        active = [cs for cs in derived.values() if cs.current_state not in TERMINAL_STATES]
        target = [active[0]] if active else []
    if ctx.obj.get("json"):
        click.echo(
            json_envelope(
                command="status",
                status="pass",
                exit_code=EXIT_OK,
                data={"changes": [asdict(cs) for cs in target]},
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
    sys.exit(EXIT_OK)
