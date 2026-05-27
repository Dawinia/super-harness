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
  ``{"gates": [{"name", "version", "source", "path"?}, ...]}``.

If `.harness/gates.yaml` is absent, only built-ins are listed (this is the
expected default state in v0.1 before later phases register their gates).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import click
import yaml

from super_harness.cli.exit_codes import EXIT_NO_CONFIG, EXIT_OK
from super_harness.cli.output import json_envelope
from super_harness.core.paths import (
    HarnessNotInitialized,
    find_harness_root,
    gates_yaml_path,
)
from super_harness.gates.registry import get_builtin, list_builtins, load_gates


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
        click.echo(str(e), err=True)
        sys.exit(EXIT_NO_CONFIG)

    yaml_path = gates_yaml_path(root)
    rows = _collect_gate_rows(yaml_path)

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


def _collect_gate_rows(yaml_path: Path) -> list[dict[str, Any]]:
    """Build the (name, version, source[, path]) rows for every visible gate.

    Builtins are read from the in-process registry (class attrs `name` /
    `version`). Plugins are first instantiated via `load_gates` so any
    config error surfaces to the user running `list`, then the yaml is
    re-parsed to map name → source path for the human/JSON display.
    """
    rows: list[dict[str, Any]] = []

    for name in list_builtins():
        cls = get_builtin(name)
        if cls is None:  # registration race / removal between calls — defensive only
            continue
        rows.append({"name": name, "version": cls.version, "source": "built-in"})

    plugin_paths = _read_plugin_paths(yaml_path, top_key="gates")
    if plugin_paths:
        for inst in load_gates(yaml_path, builtin_only=False):
            if inst.name in {r["name"] for r in rows if r["source"] == "built-in"}:
                continue
            rows.append(
                {
                    "name": inst.name,
                    "version": inst.version,
                    "source": "plugin",
                    "path": plugin_paths.get(inst.name, ""),
                }
            )
    return rows


def _read_plugin_paths(yaml_path: Path, *, top_key: str) -> dict[str, str]:
    """Map plugin id → declared `path` from the yaml.

    Returns an empty dict when the file is absent or the yaml has no plugin
    (dict-shaped) entries. Schema validation lives in `core._registry`;
    this is a display-only lookup so we keep it tolerant.
    """
    if not yaml_path.exists():
        return {}
    cfg = yaml.safe_load(yaml_path.read_text()) or {}
    entries = cfg.get(top_key, []) or []
    if not isinstance(entries, list):
        return {}
    paths: dict[str, str] = {}
    for entry in entries:
        if isinstance(entry, dict) and len(entry) == 1:
            sid, spec = next(iter(entry.items()))
            if isinstance(spec, dict) and isinstance(spec.get("path"), str):
                paths[sid] = spec["path"]
    return paths


def _render_human_table(rows: list[dict[str, Any]], *, kind: str) -> None:
    """Print an aligned ``NAME / VERSION / SOURCE`` table; empty → hint line."""
    if not rows:
        click.echo(f"No {kind} registered. (No built-ins and no .harness/{kind}.yaml.)")
        return
    name_w = max(len("NAME"), max(len(r["name"]) for r in rows))
    ver_w = max(len("VERSION"), max(len(r["version"]) for r in rows))
    click.echo(f"{'NAME':<{name_w}}  {'VERSION':<{ver_w}}  SOURCE")
    for r in rows:
        source = r["source"]
        if r["source"] == "plugin" and r.get("path"):
            source = f"plugin ({r['path']})"
        click.echo(f"{r['name']:<{name_w}}  {r['version']:<{ver_w}}  {source}")
