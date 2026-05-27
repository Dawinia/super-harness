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

__all__ = ["load_sensors", "register_builtin"]

_BUILTIN: dict[str, type[Sensor]] = {}


def register_builtin(name: str, cls: type[Sensor]) -> None:
    """Register a built-in Sensor class under a yaml-addressable name.

    Phases 5/8/11/13 will call this at import time to register their sensors.
    The registry is process-global; tests that register stubs should clean up
    or use fixtures if isolation matters.
    """
    _BUILTIN[name] = cls


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
        base_class=Sensor,  # type: ignore[type-abstract]
        builtin=_BUILTIN,
        builtin_only=builtin_only,
    )
