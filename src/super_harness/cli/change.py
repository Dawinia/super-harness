"""`change` subgroup — declare / abandon / list / resume lifecycle changes.

Per cli-command-surface §3.2:
- `change start <slug>` — emits `intent_declared` (the very first event in any
  change's history). This is also the FIRST emit site that wires the Phase 1
  `post_emit_refresh` helper (Task 1.9 / B-3 fix): every emit must keep
  state.yaml current so downstream gate decisions never see a lagged cache.
- `change abandon <slug>` — emits `intent_abandoned`. Same post-emit refresh.
- `change list` — replays events.jsonl through the reducer (no disk write) and
  prints per-change current_state. Supports `--state`, `--active`, `--archived`,
  `--abandoned` filters (mutually compatible — the `--active` shortcut excludes
  terminal states; the others positively select a state).
- `change resume <slug>` — emits an agent-ready Markdown context dump (current
  state + recent ~20 events + scope + pending sensors) consumed by Ralph Loop /
  adapter `inject_context` (adapter-architecture §3.5). v0.1 `pending_sensors`
  is always `[]` because no sensors are registered yet (Phase 3/5/8/11 wire
  them); this is the documented v0.1 contract, not a bug.

The `--framework` option on `change start` is a v0.1 no-op placeholder per the
project-wide convention (matches `init --framework`, `init --setup-github`,
`state rebuild --verify`, `event log --tail`): the value flows into the event
record so future adapter selection logic (Phase 4) can read it from history,
but no runtime adapter dispatch happens in v0.1.

Helper functions for `resume` (`_tail_events_for_change`, `_event_to_dict`,
`_render_resume_markdown`) live inline in this module — they total <60 lines
and only one command consumes them. Per Phase 2 convention they get factored
into a dedicated `cli/resume_renderer.py` only if they grow past ~80 lines.
"""
from __future__ import annotations

import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

import click

from super_harness.cli.errors import format_error
from super_harness.cli.output import json_envelope
from super_harness.core.active_change import read_active_change_id
from super_harness.core.clock import utc_now_iso
from super_harness.core.events import Actor, Event, EventSchemaError, parse_event_line
from super_harness.core.identity import resolve_identity
from super_harness.core.paths import (
    HarnessNotInitialized,
    events_path,
    find_harness_root,
)
from super_harness.core.post_emit import refresh_state_after_emit
from super_harness.core.reducer import derive_state
from super_harness.core.slug import SlugError, validate_slug
from super_harness.core.state import STATES, ChangeState
from super_harness.core.ulid import new_event_id
from super_harness.core.writer import EmitPreconditionError, EventWriter
from super_harness.exit_codes import EXIT_NO_CONFIG, EXIT_OK, EXIT_VALIDATION

# v0.1 default recent-event window for `change resume`. The number 20 mirrors
# adapter-architecture §3.5 `read_recent_events(change_id, limit=20)` so the
# Markdown dump and the (future) AgentAdapter.inject_context payload show the
# same tail length and the agent's context window is predictable.
_RESUME_RECENT_EVENT_LIMIT = 20

@click.group("change")
def change_group() -> None:
    """Declare / abandon / list lifecycle changes."""


@change_group.command("start")
@click.argument("slug")
@click.option("--description", default="", help="Human-readable change description.")
@click.option(
    "--framework",
    type=click.Choice(["openspec", "spec-kit", "superpowers", "plain"]),
    default="plain",
    help="Framework label recorded on the event "
    "(v0.1: no-op placeholder; framework adapters auto-detect at observe time.)",
)
@click.option(
    "--as",
    "as_identity",
    default=None,
    help="Author identity recorded on the event "
    "(default: env SUPER_HARNESS_ACTOR, else `git config user.email`, else `cli`).",
)
@click.pass_context
def start(
    ctx: click.Context, slug: str, description: str, framework: str,
    as_identity: str | None,
) -> None:
    """Declare a new change by emitting `intent_declared`."""
    # Slug validation runs BEFORE find_harness_root so users get a fast,
    # location-independent error on a bad slug regardless of cwd.
    try:
        validate_slug(slug)
    except SlugError as e:
        click.echo(
            format_error(
                subcommand="change start",
                message=str(e),
                hint="See cli-command-surface §2.3 for slug rules.",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    try:
        root = find_harness_root(Path(ctx.obj.get("workspace") or "."))
    except HarnessNotInitialized as e:
        click.echo(
            format_error(subcommand="change start", message=e.message, hint=e.hint),
            err=True,
        )
        sys.exit(EXIT_NO_CONFIG)
    ev = Event(
        event_id=new_event_id(),
        type="intent_declared",
        change_id=slug,
        timestamp=utc_now_iso(),
        actor=Actor(type="human", identifier=resolve_identity(root, as_identity)),
        framework=framework,  # type: ignore[arg-type]  # click.Choice restricts to Framework
        payload={"description": description or slug},
    )
    try:
        EventWriter(events_path(root)).emit(ev)
    except EmitPreconditionError as e:
        click.echo(
            format_error(subcommand="change start", message=str(e)),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    # B-3 wiring (Task 1.9): keep state.yaml current after every emit so
    # downstream gate decisions don't read a lagged cache.
    refresh_state_after_emit(root)
    click.echo(f"started change {slug}")
    sys.exit(EXIT_OK)


@change_group.command("abandon")
@click.argument("slug")
@click.option("--reason", default="", help="Optional human-readable abandonment reason.")
@click.pass_context
def abandon(ctx: click.Context, slug: str, reason: str) -> None:
    """Abandon an existing change by emitting `intent_abandoned`."""
    # Symmetric with `change start`: validate slug BEFORE find_harness_root so
    # users get a fast, actionable error on a bad slug regardless of cwd, and
    # don't fall through to the (confusing) lifecycle-state-rule error.
    try:
        validate_slug(slug)
    except SlugError as e:
        click.echo(
            format_error(
                subcommand="change abandon",
                message=str(e),
                hint="See cli-command-surface §2.3 for slug rules.",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    try:
        root = find_harness_root(Path(ctx.obj.get("workspace") or "."))
    except HarnessNotInitialized as e:
        click.echo(
            format_error(subcommand="change abandon", message=e.message, hint=e.hint),
            err=True,
        )
        sys.exit(EXIT_NO_CONFIG)
    ev = Event(
        event_id=new_event_id(),
        type="intent_abandoned",
        change_id=slug,
        timestamp=utc_now_iso(),
        actor=Actor(type="human", identifier="cli"),
        framework="plain",
        payload={"reason": reason},
    )
    try:
        EventWriter(events_path(root)).emit(ev)
    except EmitPreconditionError as e:
        click.echo(
            format_error(subcommand="change abandon", message=str(e)),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    # B-3 wiring (Task 1.9): same rationale as `change start`.
    refresh_state_after_emit(root)
    click.echo(f"abandoned {slug}")
    sys.exit(EXIT_OK)


@change_group.command("list")
@click.option(
    "--state",
    "state_filter",
    type=click.Choice(STATES),
    help="Show only changes in this state (one of the 10 lifecycle states, uppercase).",
)
@click.option("--active", is_flag=True, help="Exclude ARCHIVED + ABANDONED.")
@click.option("--archived", is_flag=True, help="Show only ARCHIVED.")
@click.option("--abandoned", is_flag=True, help="Show only ABANDONED.")
@click.pass_context
def list_cmd(
    ctx: click.Context,
    state_filter: str | None,
    active: bool,
    archived: bool,
    abandoned: bool,
) -> None:
    """List changes derived from events.jsonl with optional filtering."""
    # `--active`, `--archived`, `--abandoned` partition the state space into
    # disjoint slices; combining them never makes sense. Reject at parse time
    # so users get an actionable error instead of an always-empty result.
    if sum((active, archived, abandoned)) > 1:
        click.echo(
            format_error(
                subcommand="change list",
                message="--active, --archived, --abandoned are mutually exclusive",
                hint=(
                    "Use `--active` to exclude terminal states, "
                    "`--archived` for ARCHIVED only, or "
                    "`--abandoned` for ABANDONED only."
                ),
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    try:
        root = find_harness_root(Path(ctx.obj.get("workspace") or "."))
    except HarnessNotInitialized as e:
        click.echo(
            format_error(subcommand="change list", message=e.message, hint=e.hint),
            err=True,
        )
        sys.exit(EXIT_NO_CONFIG)
    # v0.1: always recompute from events.jsonl for read-after-write consistency.
    # Phase 8 daemon hot path should consume state.yaml instead (O(1) vs O(N)).
    derived = derive_state(events_path(root))
    rows = []
    for _cid, cs in derived.items():
        if state_filter and cs.current_state != state_filter:
            continue
        if active and cs.current_state in ("ARCHIVED", "ABANDONED"):
            continue
        if archived and cs.current_state != "ARCHIVED":
            continue
        if abandoned and cs.current_state != "ABANDONED":
            continue
        rows.append(asdict(cs))
    if ctx.obj.get("json"):
        click.echo(
            json_envelope(
                command="change list",
                status="pass",
                exit_code=EXIT_OK,
                data={"changes": rows},
            )
        )
    else:
        for r in rows:
            click.echo(f"{r['change_id']}\t{r['current_state']}\t{r['last_event_at']}")
    sys.exit(EXIT_OK)


def _tail_events_for_change(events_file: Path, slug: str, limit: int) -> list[Event]:
    """Return up to `limit` most-recent events for `slug`, chronological order.

    "Chronological" here = events.jsonl append order (per lifecycle §3.8.3 the
    append order IS causal order; timestamps are audit-only). So "oldest first"
    is whatever was appended first.

    Tolerance: skips malformed lines (same policy as the reducer per §3.8.1
    layered-validation). A resume dump that omits a malformed line is preferable
    to crashing the command.

    v0.1 reads the whole file into memory and filters in Python. For expected
    v0.1 log sizes (<10k events) this is well under 10ms. Phase 8 daemon can
    optimize with a slug-indexed offset table if profiling shows it's needed.
    """
    if not events_file.exists():
        return []
    matched: list[Event] = []
    for line in events_file.read_text().splitlines():
        if not line.strip():
            continue
        try:
            ev = parse_event_line(line)
        except EventSchemaError:
            # Reducer policy: warn + skip. Resume's caller is a human / agent
            # context dump — silent skip is fine; the reducer-driven `status`
            # already surfaces malformed lines via its own logging path.
            continue
        if ev.change_id == slug:
            matched.append(ev)
    # Append order = chronological; tail = last `limit`. Slice preserves order.
    return matched[-limit:]


def _event_to_dict(ev: Event) -> dict[str, Any]:
    """Render an Event as a JSON-serializable dict for the --json envelope.

    Uses dataclasses.asdict so nested Actor flattens correctly. Mirrors the
    on-disk events.jsonl shape (the line we'd have written) so JSON consumers
    can treat resume's recent_events identically to raw log lines.
    """
    return asdict(ev)


def _render_resume_markdown(cs: ChangeState, recent: list[Event]) -> str:
    """Render the human-readable Markdown context dump.

    Sections (in order — matches adapter-architecture §3.5 inject_context
    template so the Markdown the CLI prints and the string an AgentAdapter
    injects into a SessionStart prompt are visually consistent):

      # change <slug>
      **State / Last event / Framework** (one-line each)
      ## Recent events    (bullet list; "(none)" placeholder when empty)
      ## Scope            (bullet per key; "(none)" placeholder when {})
      ## Pending sensors  (v0.1: always "(none)")
    """
    lines: list[str] = []
    lines.append(f"# change {cs.change_id}")
    lines.append("")
    lines.append(f"**State:** {cs.current_state}")
    last_type = cs.last_event_type or "(none)"
    last_at = cs.last_event_at or "(none)"
    lines.append(f"**Last event:** {last_type} @ {last_at}")
    lines.append(f"**Framework:** {cs.framework}")
    lines.append("")
    lines.append("## Recent events")
    if recent:
        for ev in recent:
            lines.append(
                f"- {ev.type} @ {ev.timestamp} ({ev.actor.identifier})"
            )
    else:
        lines.append("(none)")
    lines.append("")
    lines.append("## Scope")
    # cs.scope is `dict[str, Any]` per ChangeState — render keys as bullets so
    # the agent sees structured fields, not a repr-dumped Python dict literal.
    # Values are rendered with `!r` (repr) so a Phase 3 agent-authored scope
    # value containing newlines (e.g. multi-line rationale) renders as a quoted
    # string literal like 'line1\nline2' instead of physically breaking the
    # bullet list and corrupting downstream Markdown parsing.
    if cs.scope:
        for key, value in cs.scope.items():
            lines.append(f"- {key}: {value!r}")
    else:
        lines.append("(none)")
    lines.append("")
    lines.append("## Pending sensors")
    # v0.1 contract: no sensors registered yet (Phase 3/5/8/11 wire them).
    # Placeholder makes the section's existence obvious so agents don't think
    # the dump is truncated.
    lines.append("(none)")
    return "\n".join(lines)


def _emit_resume(ctx: click.Context, root: Path, slug: str, cs: ChangeState) -> None:
    """Render the resume dump for a resolved (slug, ChangeState) and print it.

    Shared by the explicit-slug path and the no-arg active-change path so the
    two never drift. Honours the `--json` flag (structured envelope vs Markdown).
    """
    recent = _tail_events_for_change(
        events_path(root), slug, limit=_RESUME_RECENT_EVENT_LIMIT
    )
    if ctx.obj.get("json"):
        click.echo(
            json_envelope(
                command="change resume",
                status="pass",
                exit_code=EXIT_OK,
                data={
                    "change_id": slug,
                    "current_state": cs.current_state,
                    "framework": cs.framework,
                    "last_event_type": cs.last_event_type,
                    "last_event_at": cs.last_event_at,
                    "scope": cs.scope,
                    "recent_events": [_event_to_dict(ev) for ev in recent],
                    # v0.1 contract: always []. Phase 8+ wires sensor backlog.
                    # TODO(phase-8): wire SensorBacklog.pending_for(slug)
                    "pending_sensors": [],
                },
            )
        )
    else:
        click.echo(_render_resume_markdown(cs, recent))


@change_group.command("resume")
@click.argument("slug", required=False)
@click.pass_context
def resume(ctx: click.Context, slug: str | None) -> None:
    """Emit an agent-ready context dump for resuming a change mid-flight.

    Per cli-command-surface §2.3 / adapter-architecture §3.5 inject_context.
    Output: Markdown summarizing current state + recent ~20 events + scope +
    pending sensors. `--json` returns the same data as a structured envelope.

    Two modes:
    - **Explicit `<slug>`**: dump context for that change. An unknown slug is a
      user error (exit 2) — see below.
    - **No slug**: resolve the *active* change (most recently active non-terminal
      change, via `core.active_change.read_active_change_id`) and dump it. This powers the
      Claude Code SessionStart hook, which can't know the change_id at install
      time. Best-effort context injection: if there is no active change (or the
      resolved id has skewed out of derived state), print NOTHING and exit 0 —
      it does NOT trigger the explicit-slug unknown-slug exit-2 guard. This
      mirrors `ClaudeCodeAdapter.inject_context`'s empty-on-unknown contract.
      Note: this no-active-change path emits EMPTY stdout (NOT a JSON envelope)
      even under ``--json``, because its consumer — the Claude Code SessionStart
      hook — runs plain `change resume` and wants empty output to inject nothing.

    v0.1 caveats baked into the output:
    - `pending_sensors` is always `[]` / `(none)` — no sensors registered yet.
      Phase 3/5/8/11 will populate this from a sensor backlog.
    - Recent events are tailed by reading events.jsonl in full (acceptable for
      v0.1 log sizes; Phase 8 daemon may add a slug-indexed offset table).

    Explicit-slug unknown semantics deviate from `status`/`change list` (which
    return an empty result + exit 0): resume's purpose is to dump context FOR
    THIS SLUG. If the slug doesn't exist, there's no context to dump — surface
    that as a user error (exit 2) rather than silently returning an empty shell.
    """
    # Symmetric with `change start` / `change abandon`: validate an explicit slug
    # BEFORE find_harness_root so users get a fast, actionable error on a bad
    # slug regardless of cwd. (No slug → skip; the no-arg path resolves a real,
    # already-valid change id from state.yaml.)
    if slug is not None:
        try:
            validate_slug(slug)
        except SlugError as e:
            click.echo(
                format_error(
                    subcommand="change resume",
                    message=str(e),
                    hint="See cli-command-surface §2.3 for slug rules.",
                ),
                err=True,
            )
            sys.exit(EXIT_VALIDATION)
    try:
        root = find_harness_root(Path(ctx.obj.get("workspace") or "."))
    except HarnessNotInitialized as e:
        click.echo(
            format_error(subcommand="change resume", message=e.message, hint=e.hint),
            err=True,
        )
        sys.exit(EXIT_NO_CONFIG)

    if slug is None:
        # No-arg active-change mode (best-effort, used by SessionStart hook).
        active = read_active_change_id(root)
        if active is None:
            # No active change → nothing to inject. Silent success.
            sys.exit(EXIT_OK)
        derived = derive_state(events_path(root))
        cs = derived.get(active)
        if cs is None:
            # state.yaml named an active change that derived state no longer
            # knows (state/events skew) — stay best-effort: print nothing,
            # exit 0. MUST NOT fall through to the explicit-slug exit-2 guard.
            sys.exit(EXIT_OK)
        _emit_resume(ctx, root, active, cs)
        sys.exit(EXIT_OK)

    derived = derive_state(events_path(root))
    if slug not in derived:
        # Wording aligned with `status <unknown>` (more precise — "unknown
        # change slug" vs "unknown change") so the two identifier-query
        # commands surface the same error shape.
        click.echo(
            format_error(
                subcommand="change resume",
                message=f"unknown change slug: {slug!r}",
                hint="Run `super-harness change list` to see known changes.",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    _emit_resume(ctx, root, slug, derived[slug])
    sys.exit(EXIT_OK)
