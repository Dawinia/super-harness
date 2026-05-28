"""Shared loader for Sensor and Gate registries (sensor-gate-architecture §2.3, §3.5).

The two public registries (`super_harness.sensors.registry` and
`super_harness.gates.registry`) are near-mirrors of each other. This private helper
holds the actual yaml parsing + dynamic import logic so both call sites stay in
lockstep without sync drift.

Schema (parallel for sensors and gates), per spec §2.3:

    <top_key>:
      - some-builtin-name                    # built-in: registered via register_builtin
      - my-custom:                           # plugin: path + class dynamic import
          path: ./path/to/module.py
          class: MyComponentClass
          enabled: true                      # default true; false skips load

Behavior contract (spec §3.5 / AC-9):
- Unknown built-in name → log.warning + skip (yaml typos shouldn't crash startup).
- Broken plugin entry (missing keys, file not found, class missing or wrong base)
  → raise immediately. Plugin bugs must surface, not silently disappear.
- `builtin_only=True` → skip ALL plugin entries silently (used by tests / minimal mode).
- `enabled: false` on a plugin → skip that plugin, log at INFO.

**v0.1 plugin scope (spec §3.5, AC-9):** Plugin loading executes arbitrary code in
the daemon process. Sandboxing / permission isolation / per-component resource
limits are deferred to v0.2. Pin to v0.1 if depending on this loader; the v0.1 →
v0.2 boundary may breaking-change the plugin interface.

**Intra-package access:** Intended consumers are `sensors.registry`,
`gates.registry`, `cli.sensor`, `cli.gate`. The leading underscore signals
"shared infrastructure, not a third-party public API" — symbols listed in
`__all__` are stable across intra-package calls but may break in v0.2.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import ClassVar, Protocol, TypeVar, runtime_checkable

import yaml

from super_harness.core._plugin_loader import load_class_from_path

__all__ = ["RegistryComponent", "load_components", "read_plugin_paths"]

log = logging.getLogger(__name__)


@runtime_checkable
class RegistryComponent(Protocol):
    """Structural contract a yaml-loadable component must satisfy.

    Sensor and Gate ABCs both declare `name: ClassVar[str]` and
    `version: ClassVar[str]`, so they naturally satisfy this Protocol.
    Binding `_T` to this Protocol upgrades the type contract: mypy will
    now reject `load_components(base_class=str)` at call sites instead of
    relying on the runtime `issubclass` check inside `_load_plugin`.

    The runtime `issubclass(cls, base_class)` check in `_load_plugin`
    remains the load-bearing safety net for plugin classes (which mypy
    cannot see). Sensor / Gate also enforce non-empty `name` and
    non-default `version` via `__init_subclass__`, so the structural
    Protocol is precise enough for v0.1's needs.
    """

    name: ClassVar[str]
    version: ClassVar[str]


_T = TypeVar("_T", bound=RegistryComponent)


def load_components(
    yaml_path: Path,
    *,
    yaml_top_key: str,
    base_class: type[_T],
    builtin: dict[str, type[_T]],
    builtin_only: bool = False,
) -> list[_T]:
    """Load components from a yaml config; return instantiated objects.

    Args:
        yaml_path: Path to the yaml file (e.g. `.harness/sensors.yaml`).
            If the file does not exist, returns an empty list.
        yaml_top_key: The top-level key inside the yaml ("sensors" or "gates").
        base_class: The ABC every loaded component must subclass. Because the
            Sensor / Gate base classes are abstract, wrapper modules bind them
            once at module scope via `_BASE: type[Sensor] = Sensor  # type: ignore[type-abstract]`
            and pass `_BASE` here — see `sensors/registry.py` for the pattern.
            (`cast(type[Sensor], Sensor)` is rejected by mypy as redundant.)
        builtin: Mapping of built-in name → component class.
        builtin_only: If True, plugin entries are skipped silently. Useful for
            tests / minimal runs where contributor code shouldn't be loaded.

    Returns:
        Newly instantiated components, in the order they appeared in the yaml.

    Raises:
        ValueError: yaml schema is malformed (top key not a list, plugin entry
            has multiple keys, spec value is not a dict, plugin entry malformed).
        KeyError: A plugin spec is missing the `path` or `class` field.
        FileNotFoundError / ImportError: Plugin module file cannot be loaded.
        AttributeError: Named class not found inside the plugin module.
        TypeError: Plugin class is not a subclass of `base_class`.
    """
    if not yaml_path.exists():
        return []

    cfg = yaml.safe_load(yaml_path.read_text()) or {}
    entries = cfg.get(yaml_top_key, []) or []
    if not isinstance(entries, list):
        raise ValueError(
            f"{yaml_path}: {yaml_top_key!r} must be a list, got {type(entries).__name__}"
        )

    components: list[_T] = []
    for entry in entries:
        if isinstance(entry, str):
            _load_builtin(entry, builtin, components)
        elif isinstance(entry, dict):
            if builtin_only:
                continue
            _load_plugin(entry, yaml_path, base_class, components)
        else:
            raise ValueError(
                f"{yaml_path}: each entry under {yaml_top_key!r} must be a string "
                f"(built-in name) or a dict (plugin spec); got {type(entry).__name__}"
            )

    return components


def read_plugin_paths(yaml_path: Path, *, top_key: str) -> dict[str, str]:
    """Map plugin id → declared `path` from the yaml (display-only lookup).

    Used by `super-harness sensor list` / `gate list` (Phase 3.5 CLI) to
    annotate plugin rows with the yaml-declared source path. Schema
    validation lives in `load_components`; this helper is intentionally
    tolerant (returns `{}` for absent or malformed shapes) so it stays
    safe to call from display code paths that have already invoked the
    strict loader for error surfacing.

    Args:
        yaml_path: Path to the yaml file (e.g. `.harness/sensors.yaml`).
            Missing files yield an empty dict.
        top_key: Top-level yaml key (e.g. "sensors" or "gates").

    Returns:
        Mapping plugin-id → path string, populated only for entries that
        match the canonical `{id: {path: <str>, ...}}` plugin shape.
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


def _load_builtin(name: str, builtin: dict[str, type[_T]], components: list[_T]) -> None:
    cls = builtin.get(name)
    if cls is None:
        log.warning(
            "unknown built-in component %r (not registered); skipping. Known: %s",
            name,
            sorted(builtin.keys()),
        )
        return
    components.append(cls())


def _load_plugin(
    entry: dict[str, object],
    yaml_path: Path,
    base_class: type[_T],
    components: list[_T],
) -> None:
    if len(entry) != 1:
        raise ValueError(
            f"{yaml_path}: plugin entry must have exactly one key (the plugin id), "
            f"got {list(entry.keys())!r}. Did you accidentally hoist a config key "
            f"(e.g. `enabled`) to the outer dict?"
        )
    sid, spec = next(iter(entry.items()))
    if not isinstance(spec, dict):
        raise ValueError(
            f"{yaml_path}: plugin {sid!r} spec must be a dict, got {type(spec).__name__}"
        )

    if not spec.get("enabled", True):
        log.info("plugin %s is disabled (enabled: false); skipping", sid)
        return

    if "path" not in spec:
        raise KeyError(f"{yaml_path}: plugin {sid!r} is missing required key 'path'")
    if "class" not in spec:
        raise KeyError(f"{yaml_path}: plugin {sid!r} is missing required key 'class'")

    raw_path = spec["path"]
    raw_class = spec["class"]
    if not isinstance(raw_path, str):
        raise ValueError(
            f"{yaml_path}: plugin {sid!r} 'path' must be a string, got {type(raw_path).__name__}"
        )
    if not isinstance(raw_class, str):
        raise ValueError(
            f"{yaml_path}: plugin {sid!r} 'class' must be a string, got {type(raw_class).__name__}"
        )

    spec_path = Path(raw_path)
    if not spec_path.exists():
        raise FileNotFoundError(
            f"{yaml_path}: plugin {sid!r} path {str(spec_path)!r} does not exist"
        )

    # The import-spec dance (sys.modules eviction → load → exec → attribute
    # lookup → base-class check) lives in `load_class_from_path` so the
    # sensors/gates loader and the adapters loader stay in lockstep.
    #
    # v0.1 limitation: same-sid plugins in one yaml — or repeated load_*()
    # calls on the same yaml within one process (Phase 3.5 CLI tests will
    # trigger this) — silently shadow earlier registrations. The module_name
    # key eviction inside `load_class_from_path` re-exec's the file each call,
    # yielding fresh class objects (cleaner semantics for tests + CLI list
    # commands). Sandboxing + reload-on-mtime are deferred to v0.2
    # (sensor-gate-architecture spec §3.6 #6).
    cls = load_class_from_path(
        spec_path,
        raw_class,
        base_class,
        module_name=f"super_harness_user.{sid}",
        error_label=f"{yaml_path}: plugin {sid!r}",
    )

    components.append(cls())
