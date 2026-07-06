"""Sensor registry — load `.harness/sensors.yaml` into instantiated Sensors.

Per sensor-gate-architecture §2.3 + §3.5. **v0.1 is builtin-only** — the only
supported entry shape is a built-in name (registered via `register_builtin`):

    sensors:
      - verification-runner

  (Note: review is NOT a daemon sensor — there is no `plan-reviewer` /
  `code-reviewer` builtin. Review judgement is inferential and stays on the
  `review` CLI verbs, per the auto-review-hardening design §7; an in-daemon
  reviewer would mean the harness running an LLM, which it never does. A real
  reviewer sensor remains an unbuilt v0.2 question.)

A dict (plugin `path` + `class`) entry is rejected — loading contributor Python
in-process needs a trust/sandbox model, deferred with the plugins to v0.2.
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
    so registrations land at import time.
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


def load_sensors(yaml_path: Path) -> list[Sensor]:
    """Load built-in sensors from `yaml_path` (typically `.harness/sensors.yaml`).

    v0.1 is builtin-only: only string entries naming a built-in are supported;
    a dict (plugin path+class) entry raises ValueError (no module is imported).

    Args:
        yaml_path: Path to the sensors yaml. Returns `[]` if the file is absent.

    Returns:
        Newly instantiated Sensor subclasses in yaml order.

    Raises:
        ValueError: malformed schema, or a dict/plugin entry (unsupported in
            v0.1). See `core._registry.load_components`.
    """
    return load_components(
        yaml_path,
        yaml_top_key="sensors",
        base_class=_BASE,
        builtin=_BUILTIN,
    )
