"""Sensor registry — load `.harness/sensors.yaml` into instantiated Sensors.

Per sensor-gate-architecture §2.3 + §3.5. Supports two entry shapes:

- Built-in by name (registered via `register_builtin`):
    sensors:
      - plan-reviewer

- Plugin via dynamic import (path + class):
    sensors:
      - my-custom:
          path: ./sensors/my_custom_sensor.py
          class: MyCustomSensor
          enabled: true

**v0.1 plugin scope (spec §3.5, AC-9):** Plugin sensors execute arbitrary Python
in the daemon process. Sandboxing, permission isolation, and per-sensor resource
limits are deferred to v0.2. Pin to v0.1 if depending on this loader; the
v0.1 → v0.2 boundary may breaking-change the plugin interface.
"""

from __future__ import annotations

from pathlib import Path

from super_harness.core._registry import load_components
from super_harness.sensors import Sensor

__all__ = ["get_builtin", "list_builtins", "load_sensors", "register_builtin"]

# Sensor is abstract; mypy rejects passing it to `type[T]` parameters under
# strict mode (error: type-abstract). We bind it once here so future call
# sites (Phase 3.4 wiring, Phase 3.5 CLI, Phase 5/8/11/13 module __init__)
# can reuse `_BASE` without restating the `type: ignore` per call.
_BASE: type[Sensor] = Sensor  # type: ignore[type-abstract]

_BUILTIN: dict[str, type[Sensor]] = {}


def register_builtin(name: str, cls: type[Sensor]) -> None:
    """Register a built-in Sensor class under a yaml-addressable name.

    Phases 5/8/11/13 will call this at import time to register their sensors.
    The registry is process-global; tests that register stubs should clean up
    or use fixtures if isolation matters.

    Note: `gates.registry.register_builtin` has the same name — the symmetry
    is intentional, but a module that imports both must use an alias, e.g.
    `from super_harness.gates.registry import register_builtin as register_gate`.

    Recommended placement: call this from your sensor package's `__init__.py`
    so registrations land at import time. Do NOT call it from a plugin module
    that is also loaded via `.harness/sensors.yaml` path+class — `load_sensors`
    re-execs the module each call (with `sys.modules` eviction for clean
    semantics), so import-time `register_builtin` calls would overwrite
    `_BUILTIN[name]` with a freshly-instantiated class on every load,
    invalidating any class-identity comparisons held by earlier callers.
    """
    _BUILTIN[name] = cls


def list_builtins() -> list[str]:
    """Return names of all registered built-in sensors, sorted alphabetically.

    Phase 3.5 (`super-harness sensor list` CLI) and other introspection
    consumers should use this instead of peeking at the private `_BUILTIN`
    dict — the mutation surface (`register_builtin`) stays controlled.
    """
    return sorted(_BUILTIN)


def get_builtin(name: str) -> type[Sensor] | None:
    """Return the registered built-in Sensor class for `name`, or None.

    Read-only accessor used by `super-harness sensor list` (Phase 3.5 CLI)
    to fetch `cls.version` for display. Returns None if no built-in is
    registered under that name — callers must handle the missing case.
    """
    return _BUILTIN.get(name)


def load_sensors(yaml_path: Path, *, builtin_only: bool = False) -> list[Sensor]:
    """Load sensors from `yaml_path` (typically `.harness/sensors.yaml`).

    Args:
        yaml_path: Path to the sensors yaml. Returns `[]` if the file is absent.
        builtin_only: If True, plugin entries (path + class) are skipped
            silently. Useful for tests / safe mode where contributor code
            should not execute.

    Returns:
        Newly instantiated Sensor subclasses in yaml order.

    Raises:
        ValueError / KeyError / FileNotFoundError / ImportError /
        AttributeError / TypeError: See `core._registry.load_components`.
    """
    return load_components(
        yaml_path,
        yaml_top_key="sensors",
        base_class=_BASE,
        builtin=_BUILTIN,
        builtin_only=builtin_only,
    )
