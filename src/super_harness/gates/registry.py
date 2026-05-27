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

__all__ = ["load_gates", "register_builtin"]

_BUILTIN: dict[str, type[Gate]] = {}


def register_builtin(name: str, cls: type[Gate]) -> None:
    """Register a built-in Gate class under a yaml-addressable name.

    Later phases will call this at import time to register their gates.
    The registry is process-global; tests that register stubs should clean up
    or use fixtures if isolation matters.
    """
    _BUILTIN[name] = cls


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
        base_class=Gate,  # type: ignore[type-abstract]
        builtin=_BUILTIN,
        builtin_only=builtin_only,
    )
