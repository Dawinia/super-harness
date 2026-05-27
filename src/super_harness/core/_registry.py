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
"""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from typing import TypeVar

import yaml

__all__ = ["load_components"]

log = logging.getLogger(__name__)

_T = TypeVar("_T")


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
        base_class: The ABC every loaded component must subclass.
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

    module_name = f"super_harness_user.{sid}"
    module_spec = importlib.util.spec_from_file_location(module_name, spec_path)
    if module_spec is None or module_spec.loader is None:
        raise ImportError(f"{yaml_path}: cannot load plugin spec for {sid!r} from {spec_path}")
    mod = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(mod)

    if not hasattr(mod, raw_class):
        raise AttributeError(
            f"{yaml_path}: plugin {sid!r} module {spec_path} has no attribute {raw_class!r}"
        )
    cls = getattr(mod, raw_class)
    if not (isinstance(cls, type) and issubclass(cls, base_class)):
        raise TypeError(
            f"{yaml_path}: plugin class {raw_class!r} in {spec_path} is "
            f"not a {base_class.__name__} subclass"
        )

    components.append(cls())
