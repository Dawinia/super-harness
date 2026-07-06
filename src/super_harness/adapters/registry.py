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
`register_builtin` surface). **v0.1 is builtin-only:** an entry whose `builtin`
is not literally `true` (`false`, or the key omitted) is REJECTED with a
`ValueError` — it is NOT imported. Loading contributor Python in-process needs a
trust/sandbox model, which lands with the plugins themselves in v0.2.

**Import-cycle note:** this module imports the concrete builtin classes
(`PlainAdapter`, `ClaudeCodeAdapter`), which import only the ABCs from
`adapters/__init__`. `adapters/__init__` must stay ABC-only — do NOT import this
registry from it.

API stability: **experimental** (v0.1).
"""

from __future__ import annotations

from pathlib import Path

import yaml

from super_harness.adapters import AgentAdapter, FrameworkAdapter
from super_harness.adapters.agent.claude_code import ClaudeCodeAdapter
from super_harness.adapters.agent.codex import CodexAdapter
from super_harness.adapters.framework.openspec import OpenSpecAdapter
from super_harness.adapters.framework.plain import PlainAdapter
from super_harness.adapters.framework.superpowers import SuperpowersAdapter

__all__ = [
    "activate_with_fallback",
    "get_builtin",
    "list_builtins",
    "load_adapters",
    "register_builtin",
    "resolve_spec_plan_paths",
]

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


def resolve_spec_plan_paths(
    framework: str | None, root: Path, change_id: str
) -> tuple[str, str]:
    """Resolve ``(spec_path, plan_path)`` for ``change_id`` via its framework adapter.

    Pure path derivation — delegates to the builtin adapter's ``spec_paths``.
    Returns ``("", "")`` when ``framework`` is falsy or has no builtin adapter.

    Lives here (not in ``core``) so ``core.review_bundle`` stays free of any
    ``adapters`` import: the review-bundle assembler takes this as an injected
    resolver. See decision ``d-core-is-base`` (core is the base layer; it must
    not import the upper layers, including ``adapters``/``sensors``).
    """
    if not framework:
        return "", ""
    cls = get_builtin(framework)
    if cls is None or not issubclass(cls, FrameworkAdapter):
        return "", ""
    paths = cls().spec_paths(root, change_id)
    return paths.get("spec", ""), paths.get("plan", "")


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
        yaml.YAMLError: the file is not syntactically valid YAML (raised by the
            unguarded `yaml.safe_load` below). NOTE this derives from `Exception`,
            NOT `ValueError`, so callers building a best-effort catch tuple must
            list it explicitly alongside `ValueError`.
        ValueError: malformed schema (top key not a list, entry not a dict,
            missing/non-string `name`, a duplicate name, a non-builtin entry
            (`builtin` not literally true — custom plugins are unsupported in
            v0.1), or an unknown builtin name).
    """
    if not yaml_path.exists():
        return [], []

    cfg = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    # A valid-but-non-mapping top level (a bare list / scalar) would make the
    # `cfg.get(...)` below raise AttributeError; normalize it to a ValueError so
    # the loader's failure surface stays `yaml.YAMLError | ValueError | OSError`.
    if not isinstance(cfg, dict):
        raise ValueError(
            f"{yaml_path}: top level must be a mapping, got {type(cfg).__name__}"
        )
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

        # v0.1 is builtin-only. Reject any non-builtin entry loudly, BEFORE the
        # `enabled` check, so a disabled non-builtin can never slip through
        # silently and no path can import a user-supplied module. `builtin: true`
        # is the only accepted value (false / omitted / truthy-non-bool reject).
        if entry.get("builtin", False) is not True:
            raise ValueError(
                f"{yaml_path}: adapter {raw_name!r} is not a built-in "
                f"(builtin must be true); custom plugins are not supported in "
                f"v0.1 (builtin-only). See docs/limitations.md."
            )

        if not entry.get("enabled", True):
            continue

        _resolve_builtin(entry, raw_name, yaml_path, frameworks, agents)

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
register_builtin("codex", CodexAdapter)
register_builtin("openspec", OpenSpecAdapter)
register_builtin("superpowers", SuperpowersAdapter)
