"""Gate registry — load `.harness/gates.yaml` into instantiated Gates.

Per sensor-gate-architecture §2.3 + §3.5. Supports two entry shapes:

- Built-in by name (registered via `register_builtin`):
    gates:
      - pre-tool-use
      - pre-commit

- Plugin via dynamic import (path + class):
    gates:
      - my-custom-gate:
          path: ./gates/my_custom_gate.py
          class: MyCustomGate
          enabled: true

**v0.1 plugin scope (spec §3.5, AC-9):** Plugin gates execute arbitrary Python
in the daemon process. Sandboxing, permission isolation, and per-gate resource
limits are deferred to v0.2. Pin to v0.1 if depending on this loader; the
v0.1 → v0.2 boundary may breaking-change the plugin interface.
"""

from __future__ import annotations

from pathlib import Path

from super_harness.core._registry import load_components
from super_harness.gates import Gate
from super_harness.gates.pre_tool_use import PreToolUseGate

__all__ = ["get_builtin", "list_builtins", "load_gates", "register_builtin"]

# Gate is abstract; mypy rejects passing it to `type[T]` parameters under
# strict mode (error: type-abstract). We bind it once here so future call
# sites (Phase 3.4 wiring, Phase 3.5 CLI, later gate modules) can reuse
# `_BASE` without restating the `type: ignore` per call.
_BASE: type[Gate] = Gate  # type: ignore[type-abstract]

_BUILTIN: dict[str, type[Gate]] = {}


def register_builtin(name: str, cls: type[Gate]) -> None:
    """Register a built-in Gate class under a yaml-addressable name.

    Later phases will call this at import time to register their gates.
    The registry is process-global; tests that register stubs should clean up
    or use fixtures if isolation matters.

    Note: `sensors.registry.register_builtin` has the same name — the symmetry
    is intentional, but a module that imports both must use an alias, e.g.
    `from super_harness.sensors.registry import register_builtin as register_sensor`.

    Recommended placement: call this from your gate package's `__init__.py`
    so registrations land at import time. Do NOT call it from a plugin module
    that is also loaded via `.harness/gates.yaml` path+class — `load_gates`
    re-execs the module each call (with `sys.modules` eviction for clean
    semantics), so import-time `register_builtin` calls would overwrite
    `_BUILTIN[name]` with a freshly-instantiated class on every load,
    invalidating any class-identity comparisons held by earlier callers.
    """
    _BUILTIN[name] = cls


def list_builtins() -> list[str]:
    """Return names of all registered built-in gates, sorted alphabetically.

    Phase 3.5 (`super-harness gate list` CLI) and other introspection
    consumers should use this instead of peeking at the private `_BUILTIN`
    dict — the mutation surface (`register_builtin`) stays controlled.
    """
    return sorted(_BUILTIN)


def get_builtin(name: str) -> type[Gate] | None:
    """Return the registered built-in Gate class for `name`, or None.

    Read-only accessor used by `super-harness gate list` (Phase 3.5 CLI)
    to fetch `cls.version` for display. Returns None if no built-in is
    registered under that name — callers must handle the missing case.
    """
    return _BUILTIN.get(name)


def load_gates(yaml_path: Path, *, builtin_only: bool = False) -> list[Gate]:
    """Load gates from `yaml_path` (typically `.harness/gates.yaml`).

    Args:
        yaml_path: Path to the gates yaml. Returns `[]` if the file is absent.
        builtin_only: If True, plugin entries (path + class) are skipped
            silently. Useful for tests / safe mode where contributor code
            should not execute.

    Returns:
        Newly instantiated Gate subclasses in yaml order.

    Raises:
        ValueError / KeyError / FileNotFoundError / ImportError /
        AttributeError / TypeError: See `core._registry.load_components`.
    """
    return load_components(
        yaml_path,
        yaml_top_key="gates",
        base_class=_BASE,
        builtin=_BUILTIN,
        builtin_only=builtin_only,
    )


register_builtin("pre-tool-use", PreToolUseGate)
