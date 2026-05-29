"""`super-harness pr` command group — PR-side validation (cli-command-surface §`pr validate`).

Phase 12 ships the read-only `pr validate <num>` verdict: it pulls a PR body via
`gh pr view`, parses the super-harness metadata block (engineering-integration
§2.5), and runs three blocker checks — block complete / lifecycle sequence
violation-free / change READY_TO_MERGE. It is a pure verdict: it neither emits
events nor mutates state (it only READS `.harness/events.jsonl` for the lifecycle
checks, which is why a missing `.harness/` is EXIT_NO_CONFIG, same as verify/done).

Output convention (mirrors verify): only the pass/fail verdict (exit 0/2) emits
the frozen `json_envelope` under `--json`. The "couldn't run" exits — 3 (no
`.harness/`) and 4 (gh failure) — print `format_error` to stderr and emit NO
envelope even under `--json`, identical to verify's HarnessNotInitialized path.

Exit codes (cli-command-surface §`pr validate`):
- 0 — no blockers.
- 2 — one or more blockers (EXIT_VALIDATION).
- 3 — `.harness/` missing (EXIT_NO_CONFIG; reads events.jsonl for lifecycle checks).
- 4 — `gh pr view` failed (EXIT_EXTERNAL_TOOL).

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
    EXIT_NO_CONFIG,
    EXIT_OK,
    EXIT_VALIDATION,
)
from super_harness.cli.output import json_envelope
from super_harness.core.emit_validation import find_ordering_violations
from super_harness.core.paths import (
    HarnessNotInitialized,
    events_path,
    find_harness_root,
)
from super_harness.core.reducer import derive_state
from super_harness.engineering import gh
from super_harness.engineering.pr_metadata import (
    REQUIRED_METADATA_KEYS,
    parse_metadata_block,
)


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
