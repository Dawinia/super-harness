"""`sensor` subgroup — enumerate the sensor registry.

Per cli-command-surface §3.x and sensor-gate-architecture §2.3 (sensors.yaml
config). `sensor list` is the operator-facing discovery surface on the sensor
registry: it prints every built-in the dispatcher would load at runtime. v0.1
is builtin-only, so every row is a built-in.

Output modes:
- Human-readable (default): an aligned 3-column table with header
  ``NAME / VERSION / SOURCE``.
- JSON envelope (`--json` global flag): the standard 6-key envelope wrapping
  ``{"sensors": [{"name", "version", "source", "path"}, ...]}`` — `path` is
  always ``null`` in v0.1 but kept so JSON consumers can rely on the key.

If `.harness/sensors.yaml` is absent, only built-ins are listed (this is the
expected default state in v0.1 before Phase 5/8/11/13 register their sensors).

Error surfacing: when `.harness/sensors.yaml` exists, the strict loader
(`load_sensors`) is invoked so that yaml-schema errors — including a
dict/plugin entry, which is unsupported in v0.1 — surface to the user running
`sensor list` with EXIT_VALIDATION rather than being silently swallowed and
presenting a misleading "No sensors registered." line.
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
    sensors_yaml_path,
)
from super_harness.exit_codes import EXIT_NO_CONFIG, EXIT_OK, EXIT_VALIDATION
from super_harness.sensors.registry import get_builtin, list_builtins, load_sensors


@click.group("sensor")
def sensor_group() -> None:
    """Inspect the sensor registry."""


@sensor_group.command("list")
@click.pass_context
def sensor_list(ctx: click.Context) -> None:
    """List built-in sensors visible to the dispatcher."""
    try:
        root = find_harness_root(Path(ctx.obj.get("workspace") or "."))
    except HarnessNotInitialized as e:
        click.echo(
            format_error(subcommand="sensor list", message=e.message, hint=e.hint),
            err=True,
        )
        sys.exit(EXIT_NO_CONFIG)

    yaml_path = sensors_yaml_path(root)
    try:
        rows = _collect_sensor_rows(yaml_path)
    except (ValueError, yaml.YAMLError) as exc:
        # Translate the documented failure surface of `core._registry` — a
        # `ValueError` (malformed schema, or a dict/plugin entry unsupported in
        # v0.1) or a `yaml.YAMLError` (syntactically corrupt yaml, from the
        # unguarded `yaml.safe_load`) — to EXIT_VALIDATION. Without this, a
        # malformed sensors.yaml would print a stack trace or be silently
        # swallowed — both UX regressions.
        click.echo(
            format_error(subcommand="sensor list", message=str(exc)),
            err=True,
        )
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
    `version`). If `yaml_path` exists, `load_sensors` is invoked so that any
    yaml-shape error — including a dict/plugin entry, which is unsupported in
    v0.1 — surfaces to the caller (translated to EXIT_VALIDATION by the `list`
    command). v0.1 is builtin-only, so every listed sensor is a built-in row.

    The returned rows always include a `path` key (always `None` in v0.1) so
    downstream JSON consumers can treat the key as required.
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
        # Strict load surfaces yaml-shape / plugin-rejection errors (malformed
        # top key, or a dict/plugin entry now unsupported). Its instances are
        # all built-ins (already rowed above), so we call it only for that side
        # effect and discard the result.
        load_sensors(yaml_path)
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
