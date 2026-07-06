"""Shared loader for Sensor and Gate registries (sensor-gate-architecture §2.3, §3.5).

The two public registries (`super_harness.sensors.registry` and
`super_harness.gates.registry`) are near-mirrors of each other. This private helper
holds the actual yaml parsing + built-in resolution logic so both call sites stay
in lockstep without sync drift.

Schema (parallel for sensors and gates), per spec §2.3:

    <top_key>:
      - some-builtin-name                    # built-in: registered via register_builtin

Behavior contract (spec §3.5 / AC-9):
- Unknown built-in name (string) → log.warning + skip (yaml typos shouldn't crash startup).
- **v0.1 is builtin-only:** a dict entry (the old `{id: {path, class}}` plugin shape) is
  REJECTED with a ValueError — it is NOT imported. Loading contributor Python in-process
  needs a trust/sandbox model, which lands with the plugins themselves in v0.2.

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

__all__ = ["RegistryComponent", "load_components"]

log = logging.getLogger(__name__)


@runtime_checkable
class RegistryComponent(Protocol):
    """Structural contract a yaml-loadable component must satisfy.

    Sensor and Gate ABCs both declare `name: ClassVar[str]` and
    `version: ClassVar[str]`, so they naturally satisfy this Protocol.
    Binding `_T` to this Protocol upgrades the type contract: mypy rejects
    a `load_components(base_class=str)` call site instead of silently
    accepting it. In v0.1 (builtin-only) `base_class` is a typing-only
    device — it drives `_T` return inference and constrains call sites; the
    loader never does a runtime `issubclass` against it (only registered
    built-in names are instantiated). Sensor / Gate also enforce non-empty
    `name` and non-default `version` via `__init_subclass__`.
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
) -> list[_T]:
    """Load built-in components from a yaml config; return instantiated objects.

    v0.1 is builtin-only: every entry must be a string naming a built-in. A dict
    entry (the old `{id: {path, class}}` plugin shape) is rejected — no
    contributor module is ever imported.

    Args:
        yaml_path: Path to the yaml file (e.g. `.harness/sensors.yaml`).
            If the file does not exist, returns an empty list.
        yaml_top_key: The top-level key inside the yaml ("sensors" or "gates").
        base_class: Typing-only device that binds `_T` (the element type of the
            returned list). It is NOT read at runtime — v0.1 instantiates only
            registered built-ins, so there is no runtime `issubclass` check. It
            exists so mypy infers the right return type and rejects a mismatched
            `builtin` mapping at the call site. Because the Sensor / Gate base
            classes are abstract, wrapper modules bind them once at module scope
            via `_BASE: type[Sensor] = Sensor  # type: ignore[type-abstract]` and
            pass `_BASE` here — see `sensors/registry.py` for the pattern.
        builtin: Mapping of built-in name → component class.

    Returns:
        Newly instantiated components, in the order they appeared in the yaml.

    Raises:
        ValueError: yaml schema is malformed (top key not a list; or an entry is
            not a string — including the old dict/plugin shape, which is
            unsupported in v0.1).
    """
    if not yaml_path.exists():
        return []

    cfg = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    # A valid-but-non-mapping top level (a bare list / scalar) would make the
    # `cfg.get(...)` below raise AttributeError; normalize it to a ValueError so
    # the loader's failure surface stays `yaml.YAMLError | ValueError | OSError`.
    if not isinstance(cfg, dict):
        raise ValueError(
            f"{yaml_path}: top level must be a mapping, got {type(cfg).__name__}"
        )
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
            raise ValueError(
                f"{yaml_path}: {yaml_top_key} entry {list(entry.keys())!r} is a "
                f"plugin (path + class); custom plugins are not supported in v0.1 "
                f"(builtin-only). See docs/limitations.md."
            )
        else:
            raise ValueError(
                f"{yaml_path}: each entry under {yaml_top_key!r} must be a string "
                f"(built-in name); got {type(entry).__name__}"
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
