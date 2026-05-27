"""`change` subgroup — declare / abandon / list lifecycle changes.

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

The `--framework` option on `change start` is a v0.1 no-op placeholder per the
project-wide convention (matches `init --framework`, `init --setup-github`,
`state rebuild --verify`, `event log --tail`): the value flows into the event
record so future adapter selection logic (Phase 4) can read it from history,
but no runtime adapter dispatch happens in v0.1.
"""
from __future__ import annotations

import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import click

from super_harness.cli.exit_codes import EXIT_NO_CONFIG, EXIT_OK, EXIT_VALIDATION
from super_harness.cli.output import json_envelope
from super_harness.core.events import Actor, Event
from super_harness.core.paths import (
    HarnessNotInitialized,
    events_path,
    find_harness_root,
)
from super_harness.core.post_emit import refresh_state_after_emit
from super_harness.core.reducer import derive_state
from super_harness.core.slug import SlugError, validate_slug
from super_harness.core.state import STATES
from super_harness.core.ulid import new_event_id
from super_harness.core.writer import EmitPreconditionError, EventWriter

# TODO(post-v0.1): distinguish CLI invocations by user (getpass.getuser()) or
# session id for multi-operator audit trails. v0.1 = single "cli" identifier
# is used for every `Actor(type="human", identifier="cli")` below.


def _utc_now_iso() -> str:
    """ISO 8601 UTC with trailing `Z` (matches lifecycle-event-model §2 examples)."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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
    "(v0.1: no-op placeholder; Phase 4 wires adapter selection.)",
)
@click.pass_context
def start(ctx: click.Context, slug: str, description: str, framework: str) -> None:
    """Declare a new change by emitting `intent_declared`."""
    # Slug validation runs BEFORE find_harness_root so users get a fast,
    # location-independent error on a bad slug regardless of cwd.
    try:
        validate_slug(slug)
    except SlugError as e:
        click.echo(
            f"super-harness change start: {e}\n  Hint: see cli-reference#slug-rules",
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    try:
        root = find_harness_root(Path(ctx.obj.get("workspace") or "."))
    except HarnessNotInitialized as e:
        click.echo(str(e), err=True)
        sys.exit(EXIT_NO_CONFIG)
    ev = Event(
        event_id=new_event_id(),
        type="intent_declared",
        change_id=slug,
        timestamp=_utc_now_iso(),
        actor=Actor(type="human", identifier="cli"),
        framework=framework,  # type: ignore[arg-type]  # click.Choice restricts to Framework
        payload={"description": description or slug},
    )
    try:
        EventWriter(events_path(root)).emit(ev)
    except EmitPreconditionError as e:
        click.echo(f"super-harness change start: {e}", err=True)
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
            f"super-harness change abandon: {e}\n  Hint: see cli-reference#slug-rules",
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    try:
        root = find_harness_root(Path(ctx.obj.get("workspace") or "."))
    except HarnessNotInitialized as e:
        click.echo(str(e), err=True)
        sys.exit(EXIT_NO_CONFIG)
    ev = Event(
        event_id=new_event_id(),
        type="intent_abandoned",
        change_id=slug,
        timestamp=_utc_now_iso(),
        actor=Actor(type="human", identifier="cli"),
        framework="plain",
        payload={"reason": reason},
    )
    try:
        EventWriter(events_path(root)).emit(ev)
    except EmitPreconditionError as e:
        click.echo(f"super-harness change abandon: {e}", err=True)
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
    help="Show only changes in this state (one of the 11 lifecycle states, uppercase).",
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
            "super-harness change list: --active, --archived, --abandoned are "
            "mutually exclusive\n  Hint: pick one",
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    try:
        root = find_harness_root(Path(ctx.obj.get("workspace") or "."))
    except HarnessNotInitialized as e:
        click.echo(str(e), err=True)
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
