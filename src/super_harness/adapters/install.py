"""Platform-neutral adapter installation and config persistence services."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from super_harness.adapters import AgentAdapter
from super_harness.adapters.agent._settings_merge import (
    SettingsMergePlan,
    apply_settings_merge_plan,
)
from super_harness.core.paths import adapters_yaml_path

# Leading comment written when CREATING adapters.yaml so users know the file is
# tool-managed (mirrors state.yaml's AUTO-GENERATED header convention).
_ADAPTERS_YAML_HEADER = (
    "# .harness/adapters.yaml\n"
    "# AUTO-MANAGED by super-harness adapter install/uninstall. Do not edit.\n"
)


@dataclass(frozen=True)
class AgentIntegrationPlan:
    name: str
    hook_executable: str
    cli_executable: str
    settings: SettingsMergePlan


def _builtin_agent(name: str) -> AgentAdapter:
    from super_harness.adapters.registry import get_builtin

    cls = get_builtin(name)
    if cls is None or not issubclass(cls, AgentAdapter):
        raise ValueError(f"{name!r} is not a built-in agent integration")
    return cls()


def preview_agent_integration(
    root: Path,
    name: str,
    *,
    executable_lookup: Callable[[str], str | None] | None = None,
) -> AgentIntegrationPlan:
    """Freeze executable resolution and the exact settings bytes for review."""
    adapter = _builtin_agent(name)
    executable_lookup = executable_lookup or shutil.which
    resolved_hook = executable_lookup("super-harness-hook")
    resolved_cli = executable_lookup("super-harness")
    missing = "super-harness-hook" if resolved_hook is None else "super-harness"
    if resolved_hook is None or resolved_cli is None:
        raise RuntimeError(
            f"{missing} not found on PATH; reinstall super-harness before configuring {name}."
        )
    settings = adapter.plan_hook_install(
        root, hook_executable=resolved_hook, cli_executable=resolved_cli
    )
    if settings is None:
        raise RuntimeError(f"{name!r} does not support non-mutating hook planning")
    return AgentIntegrationPlan(name, resolved_hook, resolved_cli, settings)


def install_agent_integration(
    root: Path,
    name: str,
    *,
    plan: AgentIntegrationPlan | None = None,
) -> AgentAdapter:
    """Install one built-in coding-agent integration for init or adapter CLI.

    This wires only super-harness hooks and registry metadata. It never installs
    the coding-agent binary itself. Registry loading stays deferred until an
    integration is selected so importing the service remains platform-neutral.
    """
    adapter = _builtin_agent(name)
    frozen = plan if plan is not None else preview_agent_integration(root, name)
    if frozen.name != name:
        raise ValueError(f"integration plan for {frozen.name!r} cannot install {name!r}")
    current_hook = shutil.which("super-harness-hook")
    current_cli = shutil.which("super-harness")
    if (current_hook, current_cli) != (frozen.hook_executable, frozen.cli_executable):
        raise RuntimeError(
            "super-harness executable path changed after review; rerun init and review "
            "the refreshed integration plan."
        )
    apply_settings_merge_plan(frozen.settings)
    _persist_install_entry(root, name=adapter.name, kind="agent", version=adapter.version)
    return adapter


def _read_adapter_cfg(path: Path) -> dict[str, Any]:
    """Return the full parsed mapping from adapters.yaml ({} if absent/empty).

    Raises:
        yaml.YAMLError: if the file exists but contains invalid YAML (callers
            must catch this and surface it via ``format_error``).
    """
    if not path.exists():
        return {}
    # NOTE: yaml.YAMLError propagates — callers catch it.
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        return {}
    return loaded


def _read_adapter_entries(path: Path) -> list[dict[str, Any]]:
    """Return the list of adapter entries from adapters.yaml ([] if absent/empty).

    Raises:
        yaml.YAMLError: propagated from ``_read_adapter_cfg`` on corrupt YAML.
    """
    cfg = _read_adapter_cfg(path)
    entries = cfg.get("adapters") or []
    if not isinstance(entries, list):
        return []
    return [e for e in entries if isinstance(e, dict)]


def _write_adapter_cfg(path: Path, cfg: dict[str, Any]) -> None:
    """Write the full config mapping back to adapters.yaml (preserving top-level keys).

    Lazily creates parent directories and prepends the AUTO-MANAGED header.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(cfg, sort_keys=False, default_flow_style=False)
    path.write_text(_ADAPTERS_YAML_HEADER + body, encoding="utf-8")


def _persist_install_entry(root: Path, *, name: str, kind: str, version: str) -> None:
    """Write/update the §2.3 adapters.yaml entry — idempotent update-in-place.

    Re-installing rewrites the existing same-name entry rather than appending a
    duplicate; the file is created lazily if absent. Preserves all other
    top-level keys already present in adapters.yaml.
    """
    path = adapters_yaml_path(root)
    cfg = _read_adapter_cfg(path)
    entries: list[dict[str, Any]] = cfg.get("adapters") or []
    if not isinstance(entries, list):
        entries = []
    entries = [e for e in entries if isinstance(e, dict)]
    new_entry: dict[str, Any] = {
        "name": name,
        "type": kind,
        "builtin": True,
        "version": version,
        "enabled": True,
    }
    for idx, entry in enumerate(entries):
        if entry.get("name") == name:
            entries[idx] = new_entry
            break
    else:
        entries.append(new_entry)
    cfg["adapters"] = entries
    _write_adapter_cfg(path, cfg)


def _remove_install_entry(root: Path, *, name: str) -> None:
    """Drop the adapters.yaml entry for `name` (leaving ``adapters: []`` if empty).

    Preserves all other top-level keys already present in adapters.yaml.
    """
    path = adapters_yaml_path(root)
    cfg = _read_adapter_cfg(path)
    entries: list[dict[str, Any]] = cfg.get("adapters") or []
    if not isinstance(entries, list):
        entries = []
    entries = [e for e in entries if isinstance(e, dict)]
    cfg["adapters"] = [e for e in entries if e.get("name") != name]
    _write_adapter_cfg(path, cfg)
