"""`super-harness plan` — plain-mode `plan_ready` emitter (HG-13).

`plan ready <slug> [--scope <files-yaml>] [--tier-hint <t>]`
(cli-command-surface §418) manually emits `plan_ready`, advancing
INTENT_DECLARED / PLAN_REJECTED → AWAITING_PLAN_REVIEW. It is the plain-mode
counterpart to the framework adapters' automatic plan-artifact observation:
without it a `plain` change `change start`-ed into INTENT_DECLARED has no CLI
verb to reach AWAITING_PLAN_REVIEW, so a cold-start change is stuck at the very
first lifecycle stage (the HG-13 self-host blocker). Emit is STRICT — an illegal
transition (e.g. from PLAN_APPROVED) is rejected and nothing is appended.

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
from super_harness.core.clock import utc_now_iso
from super_harness.core.emit_validation import EmitPreconditionError
from super_harness.core.events import Actor, Event
from super_harness.core.paths import (
    HarnessNotInitialized,
    events_path,
    find_harness_root,
)
from super_harness.core.post_emit import refresh_state_after_emit
from super_harness.core.reducer import derive_state
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
        raise _ScopeError(
            f"--scope must be a yaml list of files, got {type(parsed).__name__}"
        )
    return [str(item) for item in parsed]


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
@click.pass_context
def ready(
    ctx: click.Context,
    slug: str,
    scope_raw: str | None,
    tier_hint: str | None,
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

    payload: dict[str, object] = {}
    if scope_raw is not None:
        try:
            payload["scope"] = {"files": _resolve_scope_files(scope_raw)}
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
    if tier_hint is not None:
        payload["tier_hint"] = tier_hint

    cs = derive_state(events_path(root)).get(slug)
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
