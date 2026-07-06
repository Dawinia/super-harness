"""Gate registry — load `.harness/gates.yaml` into instantiated Gates.

Per sensor-gate-architecture §2.3 + §3.5. **v0.1 is builtin-only** — the only
supported entry shape is a built-in name (registered via `register_builtin`):

    gates:
      - pre-tool-use
      - pre-commit

A dict (plugin `path` + `class`) entry is rejected — loading contributor Python
in-process needs a trust/sandbox model, deferred with the plugins to v0.2.
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
    so registrations land at import time.
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


def load_gates(yaml_path: Path) -> list[Gate]:
    """Load built-in gates from `yaml_path` (typically `.harness/gates.yaml`).

    v0.1 is builtin-only: only string entries naming a built-in are supported;
    a dict (plugin path+class) entry raises ValueError (no module is imported).

    Args:
        yaml_path: Path to the gates yaml. Returns `[]` if the file is absent.

    Returns:
        Newly instantiated Gate subclasses in yaml order.

    Raises:
        ValueError: malformed schema, or a dict/plugin entry (unsupported in
            v0.1). See `core._registry.load_components`.
    """
    return load_components(
        yaml_path,
        yaml_top_key="gates",
        base_class=_BASE,
        builtin=_BUILTIN,
    )


register_builtin("pre-tool-use", PreToolUseGate)
