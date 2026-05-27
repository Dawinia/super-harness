"""`state` subgroup — inspect / rebuild the derived state.yaml.

Per cli-command-surface §3.6:
- `state rebuild` — re-runs the reducer (Task 1.6) and rewrites state.yaml
  (Task 1.7) atomically. `--dry-run` prints derived state to stdout without
  touching disk; `--verify` is a forward-compat no-op in v0.1 (the wiring of
  rebuild-then-verify-in-one-shot is deferred — operators chain the commands).
- `state verify` — invariant checker. Replays events.jsonl through the
  reducer + transition table and reports any of:
    1. Malformed JSON / missing required fields
    2. Illegal transitions per compute_target_state
    3. Reducer non-idempotency (derive_state(events) != derive_state(events))
    4. event_counts contaminated with unknown event types
  Exit 0 = clean; exit 2 (EXIT_VALIDATION) = at least one invariant violated.
  Details go to stderr; stdout stays clean for the JSON envelope path.
"""
from __future__ import annotations

import sys
from pathlib import Path

import click

from super_harness.cli.errors import format_error
from super_harness.cli.exit_codes import EXIT_NO_CONFIG, EXIT_OK, EXIT_VALIDATION
from super_harness.cli.output import json_envelope
from super_harness.core.events import (
    KNOWN_EVENT_TYPES,
    EventSchemaError,
    parse_event_line,
)
from super_harness.core.paths import (
    HarnessNotInitialized,
    events_path,
    find_harness_root,
    state_path,
)
from super_harness.core.reducer import derive_state
from super_harness.core.state_yaml import write_state_yaml
from super_harness.core.transitions import INVALID, compute_target_state


@click.group("state")
def state_group() -> None:
    """Inspect / rebuild the derived state.yaml."""


@state_group.command("rebuild")
@click.option("--dry-run", is_flag=True, help="Print derived state to stdout instead of writing.")
@click.option(
    "--verify",
    "verify_flag",
    is_flag=True,
    help="(v0.1: no-op placeholder; chain `state verify` separately for now.)",
)
@click.pass_context
def state_rebuild(ctx: click.Context, dry_run: bool, verify_flag: bool) -> None:
    """Re-derive state.yaml from events.jsonl."""
    # `verify_flag` is intentionally unread in v0.1 — kept for forward-compat
    # so operators can pre-write `state rebuild --verify` muscle-memory.
    # v0.2 will wire it through to invoke state_verify after the write.
    _ = verify_flag
    try:
        root = find_harness_root(Path(ctx.obj.get("workspace") or "."))
    except HarnessNotInitialized as e:
        click.echo(
            format_error(subcommand="state rebuild", message=str(e)),
            err=True,
        )
        sys.exit(EXIT_NO_CONFIG)
    derived = derive_state(events_path(root))
    # last_reduced_event_id semantics per spec §3.8.2 + §3.8.3: the literal
    # event_id of the last non-blank parseable line in events.jsonl (file-position
    # truth, NOT dict-iteration order). v0.1 uses this as audit metadata; Phase 2
    # daemon consumers will rely on it for short-circuit decisions.
    last_id = ""
    events_file = events_path(root)
    if events_file.exists():
        for line in reversed(events_file.read_text().splitlines()):
            if not line.strip():
                continue
            try:
                last_id = parse_event_line(line).event_id
                break
            except EventSchemaError:
                continue
    if dry_run:
        for cid, cs in derived.items():
            click.echo(f"{cid}\t{cs.current_state}\t{cs.last_event_type}")
        sys.exit(EXIT_OK)
    write_state_yaml(state_path(root), derived, last_reduced_event_id=last_id)
    if ctx.obj.get("json"):
        click.echo(
            json_envelope(
                command="state rebuild",
                status="pass",
                exit_code=EXIT_OK,
                data={"changes": len(derived)},
            )
        )
    sys.exit(EXIT_OK)


@state_group.command("verify")
@click.pass_context
def state_verify(ctx: click.Context) -> None:
    """Validate events.jsonl invariants and reducer determinism.

    Walks events.jsonl and checks:
      1. Every non-blank line parses + has required fields
      2. Every transition is legal per compute_target_state
      3. derive_state is idempotent (run twice → equal output)
      4. event_counts contains only KNOWN_EVENT_TYPES

    Exit 0 = clean; exit 2 = invariant violation (stderr details).
    """
    try:
        root = find_harness_root(Path(ctx.obj.get("workspace") or "."))
    except HarnessNotInitialized as e:
        click.echo(
            format_error(subcommand="state verify", message=str(e)),
            err=True,
        )
        sys.exit(EXIT_NO_CONFIG)
    f = events_path(root)
    violations: list[str] = []
    parsed_events = []
    if f.exists():
        for ln, line in enumerate(f.read_text().splitlines(), start=1):
            if not line.strip():
                continue
            try:
                parsed_events.append(parse_event_line(line))
            except EventSchemaError as e:
                violations.append(f"line {ln}: malformed event ({e})")

    # Check transition legality by replaying through the table directly
    # (independent of the reducer's tolerance — verify is strict).
    states_per_change: dict[str, str | None] = {}
    for ev in parsed_events:
        prev = states_per_change.get(ev.change_id)
        new = compute_target_state(prev, ev.type)
        if new == INVALID:
            violations.append(
                f"event {ev.event_id}: illegal transition {prev} --[{ev.type}]--> ?"
            )
        else:
            states_per_change[ev.change_id] = new

    # Reducer idempotency check (invariant 1 of §3.8.5).
    s1 = derive_state(f)
    s2 = derive_state(f)
    if s1 != s2:
        violations.append(
            "reducer not idempotent (derive_state(events) != derive_state(events))"
        )

    # Invariant 5: event_counts must only contain KNOWN_EVENT_TYPES.
    for cid, cs in s1.items():
        unknown_in_counts = set(cs.event_counts) - KNOWN_EVENT_TYPES
        if unknown_in_counts:
            violations.append(
                f"change {cid}: event_counts has unknown types {sorted(unknown_in_counts)}"
            )

    if violations:
        for v in violations:
            click.echo(
                format_error(subcommand="state verify", message=v),
                err=True,
            )
        sys.exit(EXIT_VALIDATION)

    if ctx.obj.get("json"):
        click.echo(
            json_envelope(
                command="state verify",
                status="pass",
                exit_code=EXIT_OK,
                data={"events_checked": len(parsed_events), "changes": len(s1)},
            )
        )
    else:
        click.echo(f"state verify: clean ({len(parsed_events)} events, {len(s1)} changes)")
    sys.exit(EXIT_OK)
