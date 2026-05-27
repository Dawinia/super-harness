"""`sensor` subgroup — enumerate the sensor registry.

Per cli-command-surface §3.x and sensor-gate-architecture §2.3 (sensors.yaml
config). `sensor list` is the operator-facing discovery surface on the sensor
registry: it prints every component the dispatcher would load at runtime,
distinguishing built-in registrations from `.harness/sensors.yaml` plugin
entries.

Output modes:
- Human-readable (default): an aligned 3-column table with header
  ``NAME / VERSION / SOURCE``. Plugin rows annotate the source with the
  yaml-declared path so contributors can grep their config.
- JSON envelope (`--json` global flag): the standard 6-key envelope wrapping
  ``{"sensors": [{"name", "version", "source", "path"?}, ...]}``.

If `.harness/sensors.yaml` is absent, only built-ins are listed (this is the
expected default state in v0.1 before Phase 5/8/11/13 register their sensors).
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
    sensors_yaml_path,
)
from super_harness.sensors.registry import get_builtin, list_builtins, load_sensors


@click.group("sensor")
def sensor_group() -> None:
    """Inspect the sensor registry."""


@sensor_group.command("list")
@click.pass_context
def sensor_list(ctx: click.Context) -> None:
    """List built-in + plugin sensors visible to the dispatcher."""
    try:
        root = find_harness_root(Path(ctx.obj.get("workspace") or "."))
    except HarnessNotInitialized as e:
        click.echo(str(e), err=True)
        sys.exit(EXIT_NO_CONFIG)

    yaml_path = sensors_yaml_path(root)
    rows = _collect_sensor_rows(yaml_path)

    if ctx.obj.get("json"):
        click.echo(
            json_envelope(
                command="sensor list",
                status="pass",
                exit_code=EXIT_OK,
                data={"sensors": rows},
            )
        )
    else:
        _render_human_table(rows, kind="sensors")
    sys.exit(EXIT_OK)


def _collect_sensor_rows(yaml_path: Path) -> list[dict[str, Any]]:
    """Build the (name, version, source[, path]) rows for every visible sensor.

    Builtins are read from the in-process registry (class attrs `name` /
    `version`). Plugins are first instantiated via `load_sensors` so any
    config error surfaces to the user running `list`, then the yaml is
    re-parsed to map name → source path for the human/JSON display.
    """
    rows: list[dict[str, Any]] = []

    # Built-ins: name from list_builtins(), version from the class.
    for name in list_builtins():
        cls = get_builtin(name)
        if cls is None:  # registration race / removal between calls — defensive only
            continue
        rows.append({"name": name, "version": cls.version, "source": "built-in"})

    # Plugins: instantiate via the real loader (surfaces config errors here);
    # then re-read the yaml just to extract the source path for display.
    plugin_paths = _read_plugin_paths(yaml_path, top_key="sensors")
    if plugin_paths:
        for inst in load_sensors(yaml_path, builtin_only=False):
            # Built-ins are also returned by load_sensors when listed by name
            # in the yaml; skip the duplicate (already in `rows` from the
            # _BUILTIN walk above).
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
