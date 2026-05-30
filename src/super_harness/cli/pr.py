"""`super-harness pr` command group — PR-side validation + `pr_opened` emitter.

Phase 12 shipped the read-only `pr validate <num>` verdict: pulls a PR body via
`gh pr view`, parses the super-harness metadata block (engineering-integration
§2.5), and runs three blocker checks — block complete / lifecycle sequence
violation-free / change READY_TO_MERGE. It is a pure verdict: it neither emits
events nor mutates state (it only READS `.harness/events.jsonl` for the lifecycle
checks, which is why a missing `.harness/` is EXIT_NO_CONFIG, same as verify/done).

Phase 13 Task 13.7 adds `pr emit-opened` — the CI-side `pr_opened` emitter,
symmetric with `on-merge`. CI workflow on `pull_request: opened` calls
``super-harness pr emit-opened --pr <num> --change <slug>`` → emit ``pr_opened``
→ refresh state → one-shot `SensorDispatcher([PRDecorator()])` injects the §2.5
metadata block into the PR body. Internal / CI-only; humans use `pr create`.

Output convention (mirrors verify + on-merge): only the pass/fail verdict
(exit 0/2) emits the frozen `json_envelope` under `--json`. The "couldn't run"
exits — 1 (precondition / data integrity), 3 (no `.harness/`), 4 (gh failure) —
print `format_error` to stderr and emit NO envelope even under `--json`.

Exit codes (cli-command-surface §`pr validate` / §`pr emit-opened`):
- 0 — no blockers / happy path.
- 1 — `pr emit-opened` only: emit-time precondition violated.
- 2 — `pr validate` only: one or more blockers (EXIT_VALIDATION).
- 3 — `.harness/` missing (EXIT_NO_CONFIG).
- 4 — gh CLI failed (EXIT_EXTERNAL_TOOL). For `pr emit-opened` this is the
      PR-decorator-crashed path: the dispatcher's `_safe_run` caught the
      ``GhError`` and emitted ``sensor_crashed``; the CLI translates an empty
      results list into exit 4 (matches `pr validate`'s gh-failure exit).
- 5 — reserved (concurrency conflict; no v0.1 path actually exits 5).

`resolve_change_from_pr` (Fork C) is a standalone helper built now for Phase 13's
`verify --pr` wiring; `pr validate` parses inline because it needs the full block,
not just the Change field.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import click

from super_harness.cli.errors import format_error
from super_harness.cli.exit_codes import (
    EXIT_EXTERNAL_TOOL,
    EXIT_GENERIC,
    EXIT_NO_CONFIG,
    EXIT_OK,
    EXIT_VALIDATION,
)
from super_harness.cli.output import json_envelope
from super_harness.core.clock import utc_now_iso
from super_harness.core.emit_validation import find_ordering_violations
from super_harness.core.events import Actor, Event
from super_harness.core.paths import (
    HarnessNotInitialized,
    events_path,
    find_harness_root,
)
from super_harness.core.post_emit import refresh_state_after_emit
from super_harness.core.reducer import derive_state
from super_harness.core.ulid import new_event_id
from super_harness.core.writer import EmitPreconditionError, EventWriter
from super_harness.engineering import gh
from super_harness.engineering.pr_metadata import (
    REQUIRED_METADATA_KEYS,
    parse_metadata_block,
)
from super_harness.sensors import WorkspaceContext
from super_harness.sensors.dispatcher import (
    ONESHOT_DISPATCHER_PARALLELISM,
    ONESHOT_DISPATCHER_TIMEOUT_S,
    SensorDispatcher,
)
from super_harness.sensors.pr_decorator import PRDecorator

# Frozen literal sensor list for `pr emit-opened` data envelope. Matches
# on-merge's pattern: the dispatched-sensor LIST is the contract, not a
# runtime-observed outcome list. The single entry is PRDecorator.name.
_SENSORS_TRIGGERED: list[str] = [PRDecorator.name]


def resolve_change_from_pr(pr_number: int) -> str | None:
    """Resolve a change_id from a PR's metadata block, or None if there is no block.

    `view_pr → parse_metadata_block → block.fields["Change"]`. Built now for
    Phase 13's `verify --pr` wiring (which can only resolve usefully once the
    PR-decorator injects metadata blocks). `gh.GhError` is allowed to propagate —
    the Phase-13 caller handles it.
    """
    body = gh.view_pr(pr_number, fields=["body"])["body"] or ""
    block = parse_metadata_block(body)
    return block.fields.get("Change") if block.present else None


@click.group("pr")
def pr_group() -> None:
    """PR-side helpers (validate PR metadata + lifecycle)."""


@pr_group.command("validate")
@click.argument("pr_number", type=int)
@click.pass_context
def pr_validate(ctx: click.Context, pr_number: int) -> None:
    """Validate a PR's metadata block + the change's lifecycle (read-only verdict)."""
    # 1. Resolve the workspace root (walk-up, like verify/done/status). Reads
    #    events.jsonl for the lifecycle checks, so a missing .harness/ is a hard
    #    EXIT_NO_CONFIG — and, like verify, the "couldn't run" branch prints
    #    format_error to stderr and emits NO envelope even under --json.
    try:
        root = find_harness_root(Path(ctx.obj.get("workspace") or "."))
    except HarnessNotInitialized as e:
        click.echo(
            format_error(subcommand="pr validate", message=e.message, hint=e.hint),
            err=True,
        )
        sys.exit(EXIT_NO_CONFIG)

    # 2. Fetch the PR body. `--json body` can return {"body": null}; the `or ""`
    #    turns a null body into the "no block" blocker instead of a crash. A gh
    #    failure is EXIT_EXTERNAL_TOOL (no envelope, like the no-config branch).
    try:
        body = gh.view_pr(pr_number, fields=["body"])["body"] or ""
    except gh.GhError as e:
        click.echo(
            format_error(
                subcommand="pr validate",
                message=f"could not fetch PR #{pr_number}: {e}",
                hint="Check the PR number, `gh auth status`, and the current repo.",
            ),
            err=True,
        )
        sys.exit(EXIT_EXTERNAL_TOOL)

    # 3. Run the three blocker checks.
    blockers: list[str] = []
    block = parse_metadata_block(body)
    fields_complete = block.present and REQUIRED_METADATA_KEYS <= block.fields.keys()
    if not block.present:
        blockers.append("no super-harness metadata block")
    elif block.block_count >= 2:
        blockers.append("multiple metadata blocks (AC-3 violation)")
    elif not fields_complete:
        missing = sorted(REQUIRED_METADATA_KEYS - block.fields.keys())
        blockers.append(f"missing required keys {missing}")

    # Lifecycle checks are only meaningful once we resolved a change_id from the
    # block. With no block (or no Change field), change_id is None and both
    # lifecycle checks stay False — but the no-block blocker above already fired.
    change_id = block.fields.get("Change") if block.present else None
    valid_sequence = False
    merge_ready = False
    if change_id:
        # find_ordering_violations returns list[OrderingViolation]; empty-list
        # falsiness IS the "clean stream" signal — do not wrap it.
        valid_sequence = not find_ordering_violations(events_path(root), change_id)
        # State derivation: derive_state returns dict[str, ChangeState]; the
        # `.current_state` unwrap is mandatory (a bare == on the object is always
        # False). Inlined here per the plan — done._current_state is private and
        # rule-of-three is not met, so we do not import/refactor it.
        cs = derive_state(events_path(root)).get(change_id)
        current = cs.current_state if cs else None
        merge_ready = current == "READY_TO_MERGE"
        if not valid_sequence:
            blockers.append(f"lifecycle sequence invalid for {change_id}")
        if not merge_ready:
            blockers.append(f"change {change_id} not READY_TO_MERGE")

    # 4. Verdict + output.
    exit_code = EXIT_OK if not blockers else EXIT_VALIDATION
    data: dict[str, Any] = {
        "pr_number": pr_number,
        "change_id": change_id,
        "metadata_block": {"present": block.present, "fields_complete": fields_complete},
        "lifecycle_check": {"valid_sequence": valid_sequence, "merge_ready": merge_ready},
        "blockers": blockers,
    }

    if ctx.obj.get("json"):
        click.echo(
            json_envelope(
                command="pr validate",
                status="pass" if not blockers else "fail",
                exit_code=exit_code,
                data=data,
                errors=[{"code": "validation", "message": b} for b in blockers],
            )
        )
    elif not blockers:
        if not ctx.obj.get("quiet"):
            click.echo(f"PR #{pr_number} valid (change={change_id})")
    else:
        click.echo(
            format_error(
                subcommand="pr validate",
                message=f"PR #{pr_number} has {len(blockers)} blocker(s):\n  - "
                + "\n  - ".join(blockers),
                hint="Resolve each blocker, then re-run `super-harness pr validate`.",
            ),
            err=True,
        )

    sys.exit(exit_code)


# --------------------------------------------------------------------------- #
# `pr emit-opened` — CI-side `pr_opened` emitter (Phase 13 Task 13.7)
# --------------------------------------------------------------------------- #
#
# Symmetric with `on-merge` (Task 13.6); see this module's docstring + the
# cli-command-surface §`pr emit-opened` spec. Internal / CI-only.
#
# Flow:
# 1. find_harness_root → exit 3 if absent (NO --json envelope on 3).
# 2. Emit `pr_opened{change_id, pr_number}` via strict EventWriter; on
#    EmitPreconditionError → exit 1 + format_error (NO envelope).
# 3. refresh_state_after_emit (mirror on-merge so dispatcher sees fresh state).
# 4. One-shot SensorDispatcher([PRDecorator()]).on_event_emit(pr_opened_event)
#    — PR-decorator synchronously calls gh.view_pr + gh.edit_pr_body to inject
#    the §2.5 metadata block.
# 5. If PR-decorator crashed (empty results — the dispatcher's `_safe_run`
#    catches `GhError` and emits `sensor_crashed`), the CLI translates that to
#    exit 4 + format_error (NO envelope, matches verify/pr-validate's 3/4).
# 6. Otherwise exit 0; under --json emit the (v0.1 non-frozen) data envelope.


@pr_group.command("emit-opened")
@click.option(
    "--pr",
    "pr_number",
    type=int,
    required=True,
    help="PR number (CI passes ${{ github.event.pull_request.number }}).",
)
@click.option(
    "--change",
    required=True,
    help="Slug (CI passes ${{ github.head_ref }} = branch = slug, VISION convention).",
)
@click.pass_context
def pr_emit_opened(ctx: click.Context, pr_number: int, change: str) -> None:
    """Emit a ``pr_opened`` event and dispatch PR-decorator to inject metadata.

    Internal / CI-only entry; humans should use ``pr create`` (which would
    create the PR and inject metadata in one shot). v0.1 ``data`` schema for
    this command is NOT frozen by the spec (cli-command-surface §3.4 freezes
    only the 5 CI-facing commands; ``pr emit-opened`` is internal).
    """
    try:
        root = find_harness_root(Path(ctx.obj.get("workspace") or "."))
    except HarnessNotInitialized as e:
        click.echo(
            format_error(subcommand="pr emit-opened", message=e.message, hint=e.hint),
            err=True,
        )
        sys.exit(EXIT_NO_CONFIG)

    writer = EventWriter(events_path(root))
    pr_opened_event = Event(
        event_id=new_event_id(),
        type="pr_opened",
        change_id=change,
        timestamp=utc_now_iso(),
        # Distinct actor identifier from on-merge's "on-merge" so the two CI
        # emitters are distinguishable by actor.identifier in events.jsonl.
        actor=Actor(type="ci", identifier="pr-emit-opened"),
        framework="plain",
        payload={"pr_number": pr_number},
    )
    try:
        writer.emit(pr_opened_event)
    except EmitPreconditionError as e:
        # NO --json envelope on exit 1 (matches on-merge's
        # EmitPreconditionError path and verify's HarnessNotInitialized path:
        # 1/3/4 emit format_error to stderr only).
        click.echo(
            format_error(
                subcommand="pr emit-opened",
                message=str(e),
                hint=(
                    "Inspect `.harness/events.jsonl` — a change must have at "
                    "least an `intent_declared` event before `pr_opened` is legal."
                ),
            ),
            err=True,
        )
        sys.exit(EXIT_GENERIC)
    refresh_state_after_emit(root)

    ctx_ws = WorkspaceContext(
        workspace_root=root, git_branch=None, active_change_id=change
    )
    dispatcher = SensorDispatcher(
        [PRDecorator()],
        writer=writer,
        context=ctx_ws,
        timeout_s=ONESHOT_DISPATCHER_TIMEOUT_S,
        max_parallelism=ONESHOT_DISPATCHER_PARALLELISM,
    )
    # PRDecorator does NOT internally swallow gh errors (unlike L1Updater's
    # AC-7) — a GhError raised inside check() becomes a `sensor_crashed` event
    # via the dispatcher's `_safe_run`, and the dispatcher returns an empty
    # results list. We translate that to exit 4 + format_error (mirror Phase 12
    # `pr validate`'s exit-4 pattern; NO --json envelope on 4).
    results = dispatcher.on_event_emit(pr_opened_event)
    if not results:
        click.echo(
            format_error(
                subcommand="pr emit-opened",
                message=(
                    f"PR-decorator failed for PR #{pr_number} "
                    "(see `sensor_crashed` event in .harness/events.jsonl for "
                    "the underlying gh error)"
                ),
                hint=(
                    "Check `gh auth status` and the current repo; rerun "
                    "`super-harness pr emit-opened` after the external tool is fixed."
                ),
            ),
            err=True,
        )
        sys.exit(EXIT_EXTERNAL_TOOL)

    # `sensors_triggered` is the LITERAL fixed dispatched list (spec intent),
    # NOT a runtime-observed outcome. `events_emitted` is the command's OWN
    # emit (`pr_opened`); sensor-emitted events live in events.jsonl.
    data: dict[str, Any] = {
        "pr_number": pr_number,
        "change_id": change,
        "events_emitted": ["pr_opened"],
        "sensors_triggered": list(_SENSORS_TRIGGERED),
    }

    if ctx.obj.get("json"):
        click.echo(
            json_envelope(
                command="pr emit-opened",
                status="pass",
                exit_code=EXIT_OK,
                data=data,
            )
        )
    elif not ctx.obj.get("quiet"):
        click.echo(
            f"pr emit-opened: pr_opened emitted for {change} (PR #{pr_number}); "
            f"PR-decorator dispatched"
        )

    sys.exit(EXIT_OK)
