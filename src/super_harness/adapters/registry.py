"""Adapter registry — load `.harness/adapters.yaml` into instantiated adapters.

Per adapter-architecture §2.3. The adapters yaml shape is a flat list of dicts
(one row per adapter, framework + agent intermixed), which is **different** from
the sensors/gates schema — so this loader does NOT reuse `core._registry`:

    adapters:
      - {name: openspec,    type: framework, builtin: true,  version: 0.1.0, enabled: true}
      - {name: claude-code, type: agent,     builtin: true,  version: 0.1.0, enabled: true}
      - {name: my-framework, type: framework, builtin: false,
         path: ./adapters/x.py, class: MyAdapter, version: 0.0.1, enabled: true}

Built-ins are resolved from a process-global table (mirroring the sensors/gates
`register_builtin` surface); custom (`builtin: false`) entries are dynamically
imported via the shared `core._plugin_loader.load_class_from_path` primitive.

**Import-cycle note:** this module imports the concrete builtin classes
(`PlainAdapter`, `ClaudeCodeAdapter`), which import only the ABCs from
`adapters/__init__`. `adapters/__init__` must stay ABC-only — do NOT import this
registry from it.

**v0.1 plugin scope:** custom adapters execute arbitrary code in the host
process. Sandboxing / isolation deferred to v0.2.

API stability: **experimental** (v0.1).
"""

from __future__ import annotations

from pathlib import Path

import yaml

from super_harness.adapters import AgentAdapter, FrameworkAdapter
from super_harness.adapters.agent.claude_code import ClaudeCodeAdapter
from super_harness.adapters.framework.plain import PlainAdapter
from super_harness.core._plugin_loader import load_class_from_path

__all__ = [
    "activate_with_fallback",
    "get_builtin",
    "list_builtins",
    "load_adapters",
    "register_builtin",
]

# FrameworkAdapter / AgentAdapter are abstract; mypy rejects passing them to
# `type[T]` parameters (error: type-abstract) even under non-strict mode. Bind
# each once here so `load_class_from_path` calls reuse the binding without
# restating the ignore per call. (`cast(type[FrameworkAdapter], ...)` is
# rejected as redundant in the strict `core.*` scope, so this is the pattern.)
_FW_BASE: type[FrameworkAdapter] = FrameworkAdapter  # type: ignore[type-abstract]
_AG_BASE: type[AgentAdapter] = AgentAdapter  # type: ignore[type-abstract]

# A built-in is either a framework or an agent adapter class. The kind is
# derived from the actual ABC at resolution time (not the yaml `type`).
_BuiltinAdapter = type[FrameworkAdapter] | type[AgentAdapter]

_BUILTIN: dict[str, _BuiltinAdapter] = {}


def register_builtin(name: str, cls: _BuiltinAdapter) -> None:
    """Register a built-in adapter class under a yaml-addressable name.

    The registry is process-global. Both framework and agent adapters share
    this one table; `load_adapters` derives the kind from each class's actual
    ABC. (Distinct from `sensors.registry.register_builtin` /
    `gates.registry.register_builtin` — no collision, this is the adapters one.)
    """
    _BUILTIN[name] = cls


def list_builtins() -> list[str]:
    """Return names of all registered built-in adapters, sorted alphabetically."""
    return sorted(_BUILTIN)


def get_builtin(name: str) -> _BuiltinAdapter | None:
    """Return the registered built-in adapter class for `name`, or None."""
    return _BUILTIN.get(name)


def load_adapters(
    yaml_path: Path,
) -> tuple[list[FrameworkAdapter], list[AgentAdapter]]:
    """Load adapters from `yaml_path` (typically `.harness/adapters.yaml`).

    Args:
        yaml_path: Path to the adapters yaml. Returns `([], [])` if absent.

    Returns:
        A `(frameworks, agents)` tuple, each in yaml order. `enabled: false`
        entries are skipped (a disabled framework must NOT count toward
        suppressing the plain fallback — see `activate_with_fallback`).

    Raises:
        ValueError: malformed schema (top key not a list, entry not a dict,
            missing/non-string `name`, unknown builtin, missing `path`/`class`
            on a custom entry, or a same-name conflict across the union of
            resolved builtin names + all yaml names).
        FileNotFoundError / ImportError / AttributeError / TypeError: a custom
            adapter module cannot be loaded (see `load_class_from_path`).
    """
    if not yaml_path.exists():
        return [], []

    cfg = yaml.safe_load(yaml_path.read_text()) or {}
    entries = cfg.get("adapters", []) or []
    if not isinstance(entries, list):
        raise ValueError(
            f"{yaml_path}: 'adapters' must be a list, got {type(entries).__name__}"
        )

    frameworks: list[FrameworkAdapter] = []
    agents: list[AgentAdapter] = []
    # Conflict detection runs over the UNION of every yaml `name` (enabled or
    # not) — a duplicate name in the file is a config error regardless of
    # whether it would be loaded.
    seen_names: set[str] = set()

    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError(
                f"{yaml_path}: each entry under 'adapters' must be a dict, "
                f"got {type(entry).__name__}"
            )
        raw_name = entry.get("name")
        if not isinstance(raw_name, str) or not raw_name:
            raise ValueError(
                f"{yaml_path}: adapter entry is missing a non-empty string 'name'"
            )
        if raw_name in seen_names:
            raise ValueError(
                f"{yaml_path}: duplicate adapter name {raw_name!r}"
            )
        seen_names.add(raw_name)

        if not entry.get("enabled", True):
            continue

        if entry.get("builtin", False):
            _resolve_builtin(entry, raw_name, yaml_path, frameworks, agents)
        else:
            _resolve_custom(entry, raw_name, yaml_path, frameworks, agents)

    return frameworks, agents


def _resolve_builtin(
    entry: dict[str, object],
    name: str,
    yaml_path: Path,
    frameworks: list[FrameworkAdapter],
    agents: list[AgentAdapter],
) -> None:
    cls = _BUILTIN.get(name)
    if cls is None:
        # Unlike sensors/gates (which warn+skip unknown names), an unresolvable
        # adapter in the auto-managed adapters.yaml signals real corruption — fail hard.
        raise ValueError(
            f"{yaml_path}: unknown built-in adapter {name!r}; known: {list_builtins()}"
        )
    # Derive the kind from the class's ACTUAL ABC, not the yaml `type` — a
    # contradicting yaml `type` on a builtin must not mis-route it.
    instance = cls()
    if isinstance(instance, FrameworkAdapter):
        frameworks.append(instance)
    else:
        agents.append(instance)


def _resolve_custom(
    entry: dict[str, object],
    name: str,
    yaml_path: Path,
    frameworks: list[FrameworkAdapter],
    agents: list[AgentAdapter],
) -> None:
    # Conflict: a custom entry whose name shadows a builtin (plain / claude-code).
    if name in _BUILTIN:
        raise ValueError(
            f"{yaml_path}: adapter {name!r} conflicts with a built-in adapter "
            f"of the same name; rename the custom adapter"
        )

    raw_type = entry.get("type")
    if raw_type not in ("framework", "agent"):
        raise ValueError(
            f"{yaml_path}: adapter {name!r} 'type' must be 'framework' or "
            f"'agent', got {raw_type!r}"
        )
    raw_path = entry.get("path")
    raw_class = entry.get("class")
    if not isinstance(raw_path, str):
        raise ValueError(
            f"{yaml_path}: custom adapter {name!r} is missing required string 'path'"
        )
    if not isinstance(raw_class, str):
        raise ValueError(
            f"{yaml_path}: custom adapter {name!r} is missing required string 'class'"
        )

    spec_path = Path(raw_path)
    if not spec_path.exists():
        raise FileNotFoundError(
            f"{yaml_path}: adapter {name!r} path {str(spec_path)!r} does not exist"
        )

    # For custom entries we trust the yaml `type` discriminator (the class is
    # opaque until imported, so we pick the expected base from `type`).
    error_label = f"{yaml_path}: adapter {name!r}"
    module_name = f"super_harness_user.{name}"
    if raw_type == "framework":
        fw_cls = load_class_from_path(
            spec_path, raw_class, _FW_BASE,
            module_name=module_name, error_label=error_label,
        )
        frameworks.append(fw_cls())
    else:
        ag_cls = load_class_from_path(
            spec_path, raw_class, _AG_BASE,
            module_name=module_name, error_label=error_label,
        )
        agents.append(ag_cls())


def activate_with_fallback(
    frameworks: list[FrameworkAdapter], workspace: Path
) -> list[FrameworkAdapter]:
    """Resolve which frameworks are active for `workspace`, applying fallback.

    Pure function (no daemon / dispatcher wiring): the active set is every
    non-fallback framework that `detect(workspace)` returns True for. If EVERY
    non-fallback framework's `detect` returns False, the fallback adapter(s)
    (those with `is_fallback=True`, i.e. `plain`) are appended to the active
    set; otherwise fallbacks are excluded.

    Args:
        frameworks: The resolved framework list (e.g. from `load_adapters`).
        workspace: The workspace root passed to each adapter's `detect`.

    Returns:
        The active framework subset, preserving the input order.
    """
    detected = [f for f in frameworks if not f.is_fallback and f.detect(workspace)]
    if detected:
        return detected
    # Nothing detected → activate the fallback adapter(s) only.
    return [f for f in frameworks if f.is_fallback]


# --- Built-in registrations (bottom-of-module, after the table + helpers) ---
register_builtin("plain", PlainAdapter)
register_builtin("claude-code", ClaudeCodeAdapter)
