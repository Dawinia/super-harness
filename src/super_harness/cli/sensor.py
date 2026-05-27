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
  ``{"sensors": [{"name", "version", "source", "path"}, ...]}`` — `path` is
  emitted symmetrically for both built-in (always ``null``) and plugin
  (always a string) rows so JSON consumers can rely on the key existing.

If `.harness/sensors.yaml` is absent, only built-ins are listed (this is the
expected default state in v0.1 before Phase 5/8/11/13 register their sensors).

Error surfacing: when `.harness/sensors.yaml` exists, the strict loader
(`load_sensors`) is invoked unconditionally so that yaml schema / plugin
bugs surface to the user running `sensor list` with EXIT_VALIDATION rather
than being silently swallowed and presenting a misleading "No sensors
registered." line.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import click

from super_harness.cli.exit_codes import EXIT_NO_CONFIG, EXIT_OK, EXIT_VALIDATION
from super_harness.cli.output import json_envelope
from super_harness.core._registry import read_plugin_paths
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
    try:
        rows = _collect_sensor_rows(yaml_path)
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
        # a malformed sensors.yaml would either print a stack trace or be
        # silently swallowed — both UX regressions.
        click.echo(f"super-harness sensor list: {exc}", err=True)
        sys.exit(EXIT_VALIDATION)

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
    """Build the (name, version, source, path) rows for every visible sensor.

    Builtins are read from the in-process registry (class attrs `name` /
    `version`). If `yaml_path` exists, `load_sensors` is invoked
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

    # Built-ins: name from list_builtins(), version from the class.
    for name in list_builtins():
        cls = get_builtin(name)
        if cls is None:  # registration race / removal between calls — defensive only
            continue
        rows.append(
            {"name": name, "version": cls.version, "source": "built-in", "path": None}
        )

    if yaml_path.exists():
        # Invoke strict loader unconditionally to surface yaml/plugin errors
        # (malformed top key, plugin schema bugs, etc.). Display-side
        # `read_plugin_paths` then maps plugin id → source path for the
        # "path" column. Built-ins listed by name in the yaml are deduped
        # against the rows already populated above.
        instances = load_sensors(yaml_path, builtin_only=False)
        plugin_paths = read_plugin_paths(yaml_path, top_key="sensors")
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
