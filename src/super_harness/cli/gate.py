"""`gate` subgroup — enumerate the gate registry.

Per cli-command-surface §3.x and sensor-gate-architecture §2.3 (gates.yaml
config). `gate list` is the symmetric mirror of `sensor list`: same output
contract, same exit codes, same workspace-resolution semantics. It prints
every built-in gate the dispatcher would consult at runtime. v0.1 is
builtin-only, so every row is a built-in.

Output modes:
- Human-readable (default): an aligned 3-column table with header
  ``NAME / VERSION / SOURCE``.
- JSON envelope (`--json` global flag): the standard 6-key envelope wrapping
  ``{"gates": [{"name", "version", "source", "path"}, ...]}`` — `path` is
  always ``null`` in v0.1 but kept so JSON consumers can rely on the key.

If `.harness/gates.yaml` is absent, only built-ins are listed (this is the
expected default state in v0.1 before later phases register their gates).

Error surfacing: when `.harness/gates.yaml` exists, the strict loader
(`load_gates`) is invoked so that yaml-schema errors — including a dict/plugin
entry, which is unsupported in v0.1 — surface to the user running `gate list`
with EXIT_VALIDATION rather than being silently swallowed and presenting a
misleading "No gates registered." line.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import click
import yaml

from super_harness.cli.errors import format_error
from super_harness.cli.output import json_envelope
from super_harness.core.paths import (
    HarnessNotInitialized,
    find_harness_root,
    gates_yaml_path,
)
from super_harness.exit_codes import (
    EXIT_GENERIC,
    EXIT_NO_CONFIG,
    EXIT_OK,
    EXIT_VALIDATION,
)
from super_harness.gates import GateDecision, ProposedAction
from super_harness.gates.pre_tool_use import PreToolUseGate
from super_harness.gates.registry import get_builtin, list_builtins, load_gates

# The four cold-path gate names. They belong to the RATIFIED `gate check`
# command surface (cli-command-surface §gate-check lists all 5 names) but are
# not wired in v0.1 — they land with Phase 12/13.
_COLD_PATH = {"pre-commit", "pre-push", "pr-open", "pr-merge"}


@click.group("gate")
def gate_group() -> None:
    """Inspect the gate registry."""


@gate_group.command("list")
@click.pass_context
def gate_list(ctx: click.Context) -> None:
    """List built-in gates visible to the dispatcher."""
    try:
        root = find_harness_root(Path(ctx.obj.get("workspace") or "."))
    except HarnessNotInitialized as e:
        click.echo(
            format_error(subcommand="gate list", message=e.message, hint=e.hint),
            err=True,
        )
        sys.exit(EXIT_NO_CONFIG)

    yaml_path = gates_yaml_path(root)
    try:
        rows = _collect_gate_rows(yaml_path)
    except (ValueError, yaml.YAMLError) as exc:
        # Translate the documented failure surface of `core._registry` — a
        # `ValueError` (malformed schema, or a dict/plugin entry unsupported in
        # v0.1) or a `yaml.YAMLError` (syntactically corrupt yaml, from the
        # unguarded `yaml.safe_load`) — to EXIT_VALIDATION. Without this, a
        # malformed gates.yaml would print a stack trace or be silently
        # swallowed — both UX regressions.
        click.echo(
            format_error(subcommand="gate list", message=str(exc)),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)

    if ctx.obj.get("json"):
        click.echo(
            json_envelope(
                command="gate list",
                status="pass",
                exit_code=EXIT_OK,
                data={"gates": rows},
            )
        )
    else:
        _render_human_table(rows, kind="gates")
    sys.exit(EXIT_OK)


@gate_group.command("check")
@click.argument(
    "gate_name",
    type=click.Choice(
        ["pre-tool-use", "pre-commit", "pre-push", "pr-open", "pr-merge"]
    ),
)
@click.option("--tool")
@click.option("--file")
@click.option("--change-id")
@click.option("--pr", type=int)
@click.pass_context
def gate_check(
    ctx: click.Context,
    gate_name: str,
    tool: str | None,
    file: str | None,
    change_id: str | None,
    pr: int | None,
) -> None:
    """Check a gate decision (`pre-tool-use` decides in-process).

    Manual/CI/debug entry to the pre-tool-use gate; the click-less
    `super-harness-hook` binary is the hot path. Both decide **in-process**
    through `load_state_snapshot` + `PreToolUseGate` — NO daemon.
    The four cold-path gate names (`pre-commit`, `pre-push`,
    `pr-open`, `pr-merge`) are part of the ratified command surface but are not
    yet wired in v0.1 (Phase 12/13).
    """
    try:
        root = find_harness_root(Path(ctx.obj.get("workspace") or "."))
    except HarnessNotInitialized as e:
        click.echo(
            format_error(subcommand="gate check", message=e.message, hint=e.hint),
            err=True,
        )
        sys.exit(EXIT_NO_CONFIG)

    if gate_name in _COLD_PATH:
        click.echo(
            format_error(
                subcommand="gate check",
                message=(
                    f"gate '{gate_name}' not yet implemented in v0.1 "
                    "(cold-path gates land with Phase 12/13)"
                ),
            ),
            err=True,
        )
        sys.exit(EXIT_GENERIC)

    from super_harness.core.state_snapshot import load_state_snapshot

    snapshot = load_state_snapshot(root, change_id_override=change_id)
    result = PreToolUseGate().decide(
        ProposedAction(kind="edit", file=file), snapshot.state, []
    )
    allow = result.decision is GateDecision.ALLOW
    current_state = snapshot.state.current_state if snapshot.state is not None else None

    if ctx.obj.get("json"):
        click.echo(
            json_envelope(
                command="gate check",
                status="pass" if allow else "fail",
                exit_code=EXIT_OK if allow else EXIT_VALIDATION,
                data={
                    "gate_name": gate_name,
                    "decision": result.decision.value,
                    "current_state": current_state,
                    "reason": result.reason,
                    "suggested_action": result.suggested_action,
                },
            )
        )
    elif not ctx.obj.get("quiet"):
        click.echo(f"{result.decision.value}: {result.reason}")

    sys.exit(EXIT_OK if allow else EXIT_VALIDATION)


def _collect_gate_rows(yaml_path: Path) -> list[dict[str, Any]]:
    """Build the (name, version, source, path) rows for every visible gate.

    Builtins are read from the in-process registry (class attrs `name` /
    `version`). If `yaml_path` exists, `load_gates` is invoked so that any
    yaml-shape error — including a dict/plugin entry, which is unsupported in
    v0.1 — surfaces to the caller (translated to EXIT_VALIDATION by the `list`
    command). v0.1 is builtin-only, so every listed gate is a built-in row.

    The returned rows always include a `path` key (always `None` in v0.1) so
    downstream JSON consumers can treat the key as required.
    """
    rows: list[dict[str, Any]] = []

    for name in list_builtins():
        cls = get_builtin(name)
        if cls is None:  # registration race / removal between calls — defensive only
            continue
        rows.append(
            {"name": name, "version": cls.version, "source": "built-in", "path": None}
        )

    if yaml_path.exists():
        # Strict load surfaces yaml-shape / plugin-rejection errors; its
        # instances are all built-ins (already rowed above), so we call it only
        # for that side effect and discard the result.
        load_gates(yaml_path)
    return rows


def _render_human_table(rows: list[dict[str, Any]], *, kind: str) -> None:
    """Print an aligned ``NAME / VERSION / SOURCE`` table; empty → hint line."""
    if not rows:
        click.echo(f"No {kind} registered.")
        return
    name_w = max(len("NAME"), max(len(r["name"]) for r in rows))
    ver_w = max(len("VERSION"), max(len(r["version"]) for r in rows))
    click.echo(f"{'NAME':<{name_w}}  {'VERSION':<{ver_w}}  SOURCE")
    for r in rows:
        click.echo(f"{r['name']:<{name_w}}  {r['version']:<{ver_w}}  {r['source']}")
