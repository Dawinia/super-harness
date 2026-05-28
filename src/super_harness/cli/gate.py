"""`gate` subgroup — enumerate the gate registry.

Per cli-command-surface §3.x and sensor-gate-architecture §2.3 (gates.yaml
config). `gate list` is the symmetric mirror of `sensor list`: same output
contract, same exit codes, same workspace-resolution semantics. It prints
every gate the dispatcher would consult at runtime, distinguishing built-in
registrations from `.harness/gates.yaml` plugin entries.

Output modes:
- Human-readable (default): an aligned 3-column table with header
  ``NAME / VERSION / SOURCE``. Plugin rows annotate the source with the
  yaml-declared path so contributors can grep their config.
- JSON envelope (`--json` global flag): the standard 6-key envelope wrapping
  ``{"gates": [{"name", "version", "source", "path"}, ...]}`` — `path` is
  emitted symmetrically for both built-in (always ``null``) and plugin
  (always a string) rows so JSON consumers can rely on the key existing.

If `.harness/gates.yaml` is absent, only built-ins are listed (this is the
expected default state in v0.1 before later phases register their gates).

Error surfacing: when `.harness/gates.yaml` exists, the strict loader
(`load_gates`) is invoked unconditionally so that yaml schema / plugin
bugs surface to the user running `gate list` with EXIT_VALIDATION rather
than being silently swallowed and presenting a misleading "No gates
registered." line.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import click
import yaml

from super_harness.cli.errors import format_error
from super_harness.cli.exit_codes import (
    EXIT_GENERIC,
    EXIT_NO_CONFIG,
    EXIT_OK,
    EXIT_VALIDATION,
)
from super_harness.cli.output import json_envelope
from super_harness.core._registry import read_plugin_paths
from super_harness.core.active_change import read_active_change_id
from super_harness.core.paths import (
    HarnessNotInitialized,
    find_harness_root,
    gates_yaml_path,
    state_path,
)
from super_harness.core.state import ChangeState
from super_harness.core.state_yaml import read_state_yaml
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
    """List built-in + plugin gates visible to the dispatcher."""
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
    except (
        ValueError,
        KeyError,
        FileNotFoundError,
        ImportError,
        AttributeError,
        TypeError,
    ) as exc:
        # Translate the documented exception surface of `core._registry`
        # (see load_components docstring) to EXIT_VALIDATION. Without this,
        # a malformed gates.yaml would either print a stack trace or be
        # silently swallowed — both UX regressions.
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

    Manual/CI/debug entry to the pre-tool-use gate — NOT the hot path (that's
    the click-less `super-harness-hook` binary, which talks to the daemon).
    Per `cli-command-surface` §gate-check this is a non-hot-path CLI: it reads
    `state.yaml` in-process and runs `PreToolUseGate` directly with NO daemon
    dependency. The four cold-path gate names (`pre-commit`, `pre-push`,
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

    cid = change_id or read_active_change_id(root)
    state = _read_change_state(root, cid)
    result = PreToolUseGate().decide(ProposedAction(kind="edit", file=file), state, [])
    allow = result.decision is GateDecision.ALLOW
    current_state = state.current_state if state is not None else None

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


def _read_change_state(root: Path, cid: str | None) -> ChangeState | None:
    """Reconstruct the active change's `ChangeState` from state.yaml in-process.

    Returns None (→ gate ALLOWs, "no active change") when there is no resolved
    change id, when state.yaml is missing/unreadable, or when the change is
    absent from the derived `changes` map. state.yaml stores each change as a
    plain `asdict()` mapping (no dataclass reconstruction on read — see
    `state_yaml.read_state_yaml`), so we rebuild `ChangeState(**inner)` here.
    """
    if cid is None:
        return None
    path = state_path(root)
    if not path.exists():
        return None
    try:
        data = read_state_yaml(path)
    except (ValueError, OSError, yaml.YAMLError):
        return None
    changes = data.get("changes")
    if not isinstance(changes, dict):
        return None
    record = changes.get(cid)
    if not isinstance(record, dict):
        return None
    try:
        return ChangeState(**record)
    except TypeError:
        return None


def _collect_gate_rows(yaml_path: Path) -> list[dict[str, Any]]:
    """Build the (name, version, source, path) rows for every visible gate.

    Builtins are read from the in-process registry (class attrs `name` /
    `version`). If `yaml_path` exists, `load_gates` is invoked
    unconditionally so that any yaml-shape or plugin error surfaces to the
    caller (translated to EXIT_VALIDATION by the `list` command). After the
    strict load succeeds, the yaml is re-parsed display-side via
    `read_plugin_paths` to map plugin id → source path for the
    human/JSON output.

    The returned rows always include a `path` key (string for plugins,
    `None` for built-ins) so downstream JSON consumers can treat the key
    as required.
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
        instances = load_gates(yaml_path, builtin_only=False)
        plugin_paths = read_plugin_paths(yaml_path, top_key="gates")
        builtin_names = {r["name"] for r in rows}
        for inst in instances:
            if inst.name in builtin_names:
                continue
            rows.append(
                {
                    "name": inst.name,
                    "version": inst.version,
                    "source": "plugin",
                    "path": plugin_paths.get(inst.name),
                }
            )
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
        source = r["source"]
        if r["source"] == "plugin" and r.get("path"):
            source = f"plugin ({r['path']})"
        click.echo(f"{r['name']:<{name_w}}  {r['version']:<{ver_w}}  {source}")
