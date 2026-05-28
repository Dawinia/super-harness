"""Safe `.claude/settings.json` merge for the super-harness PreToolUse hook.

Registers super-harness's PreToolUse hook entry into a Claude Code
`settings.json` WITHOUT clobbering the user's existing config — merge, not
overwrite (adapter-architecture §3.5 install steps + OI-9):

- Back up a pre-existing file FIRST to
  ``settings.json.super-harness-backup.<unix-ts>`` (same directory).
- Preserve all other top-level keys and all other hook event types.
- Dedupe by exact command string — idempotent, a repeat install never stacks.
- Fail loud (clear ``ValueError``) on a malformed user file the user must fix,
  rather than crashing with an opaque ``TypeError``.

stdlib-only (`json`, `time`, `pathlib`).

API stability: **experimental** (v0.1). Used by the ClaudeCodeAdapter.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

__all__ = [
    "merge_pre_tool_use_hook",
]

# The PreToolUse matcher super-harness registers (sensor-gate §3.2.1). Covers
# every file-mutating tool Claude Code exposes.
_MATCHER = "Edit|Write|MultiEdit|NotebookEdit"
# Per-hook timeout (seconds) for the gate command, per the hook entry shape.
_TIMEOUT = 10


def merge_pre_tool_use_hook(settings_path: Path, *, command: str) -> None:
    """Register super-harness's PreToolUse hook in ``settings_path``, safely.

    If ``settings_path`` exists it is read, parsed, and backed up FIRST before
    any write. If it is absent, merging starts from an empty config (creating
    the parent directory). The new entry is appended only if no existing
    PreToolUse hook already uses ``command`` (dedupe → idempotent).

    Raises:
        ValueError: if the file is not valid JSON, is not a JSON object, or has
            a malformed ``hooks`` / ``hooks.PreToolUse`` shape the user must fix.
    """
    settings: dict[str, Any]
    if settings_path.exists():
        settings = _read_settings(settings_path)
        # Back up the pre-existing file BEFORE writing anything (OI-9).
        _write_backup(settings_path)
    else:
        settings = {}
        settings_path.parent.mkdir(parents=True, exist_ok=True)

    hooks = _ensure_hooks_dict(settings)
    pre_tool_use = _ensure_pre_tool_use_list(hooks)

    if not _has_command(pre_tool_use, command):
        pre_tool_use.append(_hook_entry(command))

    settings_path.write_text(json.dumps(settings, indent=2) + "\n")


def _read_settings(settings_path: Path) -> dict[str, Any]:
    raw = settings_path.read_text()
    try:
        parsed: Any = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{settings_path} is not valid JSON ({exc}); fix or remove it before "
            f"installing the super-harness hook."
        ) from exc
    if not isinstance(parsed, dict):
        raise ValueError(
            f"{settings_path} must be a JSON object at top level, got "
            f"{type(parsed).__name__}; fix it before installing the hook."
        )
    return parsed


def _write_backup(settings_path: Path) -> None:
    backup = settings_path.with_name(
        f"{settings_path.name}.super-harness-backup.{int(time.time())}"
    )
    backup.write_text(settings_path.read_text())


def _ensure_hooks_dict(settings: dict[str, Any]) -> dict[str, Any]:
    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError(
            f'"hooks" must be a JSON object, got {type(hooks).__name__}; the '
            f"settings file looks corrupt — fix it before installing the hook."
        )
    return hooks


def _ensure_pre_tool_use_list(hooks: dict[str, Any]) -> list[Any]:
    pre_tool_use = hooks.setdefault("PreToolUse", [])
    if not isinstance(pre_tool_use, list):
        raise ValueError(
            f'"hooks.PreToolUse" must be a JSON array, got '
            f"{type(pre_tool_use).__name__}; the settings file looks corrupt — "
            f"fix it before installing the hook."
        )
    return pre_tool_use


def _has_command(pre_tool_use: list[Any], command: str) -> bool:
    for entry in pre_tool_use:
        if not isinstance(entry, dict):
            continue
        inner = entry.get("hooks")
        if not isinstance(inner, list):
            continue
        for hook in inner:
            if isinstance(hook, dict) and hook.get("command") == command:
                return True
    return False


def _hook_entry(command: str) -> dict[str, Any]:
    return {
        "matcher": _MATCHER,
        "hooks": [{"type": "command", "command": command, "timeout": _TIMEOUT}],
    }
