"""`anchor` subgroup — manage the @capability sentinel index.

Per sensor-gate-architecture §3.1.10 (Phase 11 I-1/I-2 fix). This is the
only LIVE v0.1 entry point for the AnchorIndexRebuilder — no lifecycle event
automatically triggers the rebuilder in v0.1 (the `merged` trigger gate is
wired but the event is never emitted in the normal flow).

Subcommands:
- `anchor sync`  — rebuild `.harness/anchors/index.yaml` on demand.
- `anchor list`  — read the index and print a table of anchor → file:line rows.
  Supports `--capability <id>` (filter to one id) and `--missing-sentinel`
  (cross-ref declared anchors against the active change's affected_anchors).

Exit codes (cli-command-surface §2.2):
- 0 — success (including "absent index" or "no active change" informational paths)
- 3 — .harness/ missing (EXIT_NO_CONFIG) OR corrupt/unreadable index.yaml
"""
from __future__ import annotations

import sys
from pathlib import Path

import click
import yaml

from super_harness.cli.errors import format_error
from super_harness.cli.exit_codes import EXIT_GENERIC, EXIT_NO_CONFIG, EXIT_OK
from super_harness.core.active_change import read_active_change_id
from super_harness.core.paths import (
    HarnessNotInitialized,
    anchors_index_path,
    events_path,
    find_harness_root,
)
from super_harness.core.reducer import derive_state
from super_harness.sensors.anchor_index_rebuilder import rebuild_anchor_index


@click.group("anchor")
def anchor_group() -> None:
    """Manage the @capability sentinel location index."""


@anchor_group.command("sync")
@click.pass_context
def anchor_sync(ctx: click.Context) -> None:
    """Rebuild .harness/anchors/index.yaml by scanning all source files.

    Calls rebuild_anchor_index directly (no lifecycle event required). Use this
    for CI validation and manual recovery.
    """
    try:
        root = find_harness_root(Path(ctx.obj.get("workspace") or "."))
    except HarnessNotInitialized as e:
        click.echo(
            format_error(subcommand="anchor sync", message=e.message, hint=e.hint),
            err=True,
        )
        sys.exit(EXIT_NO_CONFIG)

    # The rebuild WRITES .harness/anchors/index.yaml (mkdir + write_text). A
    # read-only / permission-restricted .harness/ (CI sandbox, read-only mount)
    # raises OSError — surface it through format_error like the sibling write
    # commands (init maps write OSError → EXIT_GENERIC), never a raw traceback.
    try:
        result = rebuild_anchor_index(root)
    except OSError as e:
        click.echo(
            format_error(
                subcommand="anchor sync",
                message=f"could not write anchors/index.yaml: {e}",
                hint="Check that .harness/ is writable.",
            ),
            err=True,
        )
        sys.exit(EXIT_GENERIC)
    click.echo(result.summary)
    sys.exit(EXIT_OK)


@anchor_group.command("list")
@click.option(
    "--capability",
    "capability",
    default=None,
    metavar="<id>",
    help="Restrict output to a single anchor id.",
)
@click.option(
    "--missing-sentinel",
    "missing_sentinel",
    is_flag=True,
    help=(
        "Cross-reference declared anchors (from the active change's affected_anchors)"
        " against the index and report any that have no sentinel in the codebase."
        " NOTE: in v0.1 affected_anchors is typically empty because no emitter"
        " populates it yet — this flag usually reports nothing on real data."
    ),
)
@click.pass_context
def anchor_list(
    ctx: click.Context,
    capability: str | None,
    missing_sentinel: bool,
) -> None:
    """Print a table of anchor_id → file:line rows from the index.

    Index absent → friendly message + exit 0 (suggest `anchor sync`).
    Index corrupt/unreadable → error on stderr + exit 3 (EXIT_NO_CONFIG).
    """
    try:
        root = find_harness_root(Path(ctx.obj.get("workspace") or "."))
    except HarnessNotInitialized as e:
        click.echo(
            format_error(subcommand="anchor list", message=e.message, hint=e.hint),
            err=True,
        )
        sys.exit(EXIT_NO_CONFIG)

    index_path = anchors_index_path(root)

    # --- absent index: normal, not an error ---
    if not index_path.exists():
        click.echo(
            "No anchor index yet. Run `super-harness anchor sync` to build it."
        )
        sys.exit(EXIT_OK)

    # --- load index (catch corrupt / non-UTF-8 / bad YAML) ---
    # UnicodeDecodeError is a ValueError (NOT an OSError) — must be listed explicitly.
    try:
        raw = index_path.read_text(encoding="utf-8")
        data = yaml.safe_load(raw) or {}
    except (yaml.YAMLError, OSError, UnicodeDecodeError) as exc:
        click.echo(
            format_error(
                subcommand="anchor list",
                message=f"anchors/index.yaml is unreadable or corrupt: {exc}",
                hint="Run `super-harness anchor sync` to regenerate it.",
            ),
            err=True,
        )
        sys.exit(EXIT_NO_CONFIG)

    if not isinstance(data, dict):
        click.echo(
            format_error(
                subcommand="anchor list",
                message="anchors/index.yaml has an unexpected shape (not a mapping)",
                hint="Run `super-harness anchor sync` to regenerate it.",
            ),
            err=True,
        )
        sys.exit(EXIT_NO_CONFIG)

    anchors_raw = data.get("anchors") or {}
    if not isinstance(anchors_raw, dict):
        click.echo(
            format_error(
                subcommand="anchor list",
                message="anchors/index.yaml has an unexpected shape (not a mapping)",
                hint="Run `super-harness anchor sync` to regenerate it.",
            ),
            err=True,
        )
        sys.exit(EXIT_NO_CONFIG)
    anchors: dict[str, list[dict[str, object]]] = anchors_raw

    # Each anchor value must be a list of {file, line} mappings; anything else is a
    # corrupt / hand-edited index — the same error family as the non-mapping guards
    # above, one level deeper. Route to exit 3 instead of crashing on `.get` (a
    # non-dict row) or iteration (a non-list value). `isinstance(locs, list)` short-
    # circuits so a scalar `locs` never reaches the inner iteration.
    if not all(
        isinstance(locs, list) and all(isinstance(loc, dict) for loc in locs)
        for locs in anchors.values()
    ):
        click.echo(
            format_error(
                subcommand="anchor list",
                message=(
                    "anchors/index.yaml has malformed anchor locations "
                    "(expected a list of {file, line} mappings)"
                ),
                hint="Run `super-harness anchor sync` to regenerate it.",
            ),
            err=True,
        )
        sys.exit(EXIT_NO_CONFIG)

    # ------------------------------------------------------------------ #
    # --missing-sentinel mode                                              #
    # ------------------------------------------------------------------ #
    if missing_sentinel:
        change_id = read_active_change_id(root)
        if change_id is None:
            click.echo(
                "No active change to compare — skipping missing-sentinel check."
            )
            sys.exit(EXIT_OK)

        state_map = derive_state(events_path(root))
        cs = state_map.get(change_id)
        declared: list[str] = cs.affected_anchors if cs is not None else []

        # NOTE (v0.1): affected_anchors is populated by plan_ready payload, but
        # no emitter fills it in the normal flow yet — declared is typically [].
        missing = [aid for aid in declared if aid not in anchors]
        if not missing:
            click.echo(
                f"No missing sentinels for change '{change_id}' "
                f"({len(declared)} declared anchor(s) all present in the index)."
            )
        else:
            click.echo(
                f"Anchors declared for change '{change_id}' "
                f"but not found in the index ({len(missing)}):"
            )
            for aid in sorted(missing):
                click.echo(f"  {aid}")
        sys.exit(EXIT_OK)

    # ------------------------------------------------------------------ #
    # Default / --capability mode                                          #
    # ------------------------------------------------------------------ #
    if capability is not None:
        if capability not in anchors:
            click.echo(
                f"Anchor '{capability}' not found in the index."
                " Run `super-harness anchor sync` if the index is stale."
            )
            sys.exit(EXIT_OK)
        display = {capability: anchors[capability]}
    else:
        display = dict(sorted(anchors.items()))

    if not display:
        click.echo(
            "No anchors in the index."
            " Run `super-harness anchor sync` if the index is stale."
        )
        sys.exit(EXIT_OK)

    for anchor_id, locations in display.items():
        for loc in locations:
            file_val = loc.get("file", "")
            line_val = loc.get("line", "")
            click.echo(f"{anchor_id}\t{file_val}:{line_val}")

    sys.exit(EXIT_OK)
