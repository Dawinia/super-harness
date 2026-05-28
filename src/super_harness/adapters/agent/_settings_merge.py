"""Safe `.claude/settings.json` merge for the super-harness PreToolUse hook.

Registers super-harness's PreToolUse hook entry into a Claude Code
`settings.json` WITHOUT clobbering the user's existing config — merge, not
overwrite (adapter-architecture §3.5 install steps + OI-9):

- Compute the desired settings state, then write ONLY if it differs from what
  was on disk (idempotent — a no-op re-install touches nothing, no backup).
- **Replace, don't accumulate**: any prior super-harness PreToolUse entry is
  stripped before the fresh one is appended, identified by the stable
  ``--agent claude-code`` marker substring (NOT the full binary path) so a
  relocated binary (e.g. after ``pipx reinstall``) replaces the stale entry
  instead of leaving a dangling duplicate.
- Preserve all other top-level keys and all other hook event types.
- Back up a pre-existing file FIRST — only when we are actually changing it —
  to a collision-proof ``settings.json.super-harness-backup.<time_ns>`` (bumped
  if it somehow already exists) so two installs in the same wall-clock instant
  can never overwrite each other's backup (bug: same-second backup clobber).
- Fail loud (clear ``ValueError``) on a malformed user file the user must fix,
  rather than crashing with an opaque ``TypeError``.

stdlib-only (`json`, `time`, `copy`, `pathlib`).

API stability: **experimental** (v0.1). Used by the ClaudeCodeAdapter.
"""

from __future__ import annotations

import copy
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
# Stable marker that identifies a hook command as super-harness's, independent
# of the (install-location-dependent) binary path. Used to dedupe/replace our
# own prior entries across binary relocations.
_OURS_MARKER = "--agent claude-code"


def merge_pre_tool_use_hook(settings_path: Path, *, command: str) -> None:
    """Register super-harness's PreToolUse hook in ``settings_path``, safely.

    Computes the desired settings (existing config with any prior super-harness
    PreToolUse entry replaced by a fresh one for ``command``) and writes it only
    if it differs from what is on disk — so a true no-op re-install touches
    nothing. When a change IS made and the file pre-existed, the original is
    backed up FIRST to a collision-proof
    ``settings.json.super-harness-backup.<time_ns>`` path. An absent file starts
    from ``{}`` (creating the parent directory) and writes no backup.

    Raises:
        ValueError: if the file is not valid JSON, is not a JSON object, or has
            a malformed ``hooks`` / ``hooks.PreToolUse`` shape the user must fix.
    """
    existed = settings_path.exists()
    if existed:
        original = _read_settings(settings_path)
    else:
        original = {}

    # Work on a deep copy so `original` stays the exact on-disk state to compare
    # against (and to back up) — we never mutate it.
    settings = copy.deepcopy(original)
    hooks = _ensure_hooks_dict(settings)
    pre_tool_use = _ensure_pre_tool_use_list(hooks)

    _strip_super_harness_entries(pre_tool_use)
    pre_tool_use.append(_hook_entry(command))

    # `_ensure_hooks_dict` / `_ensure_pre_tool_use_list` use setdefault, which
    # can add empty "hooks"/"PreToolUse" scaffolding to `settings` that was not
    # in `original`; the desired state always has a non-empty PreToolUse list, so
    # the structural comparison below stays meaningful.
    if settings == original:
        # True idempotent re-install: nothing changed. No backup, no rewrite.
        return

    if existed:
        _write_backup(settings_path)
    else:
        settings_path.parent.mkdir(parents=True, exist_ok=True)

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
    """Back up ``settings_path`` to a collision-proof timestamped sibling.

    Uses ``time.time_ns()`` (nanosecond resolution) so two installs in the same
    wall-clock second get distinct names; if the chosen path somehow already
    exists, bump until unique so a backup can never silently overwrite another.
    """
    stamp = time.time_ns()
    backup = settings_path.with_name(
        f"{settings_path.name}.super-harness-backup.{stamp}"
    )
    while backup.exists():
        stamp += 1
        backup = settings_path.with_name(
            f"{settings_path.name}.super-harness-backup.{stamp}"
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


def _strip_super_harness_entries(pre_tool_use: list[Any]) -> None:
    """Remove super-harness's own prior PreToolUse hooks, in place.

    For each entry, drop any hook whose ``command`` contains the stable
    ``--agent claude-code`` marker (regardless of binary path). If stripping our
    hook(s) leaves an entry with an empty ``hooks`` list, drop the whole entry.
    Non-super-harness entries (and any entry with surviving hooks) are kept
    untouched, preserving their order.
    """
    survivors: list[Any] = []
    for entry in pre_tool_use:
        if not isinstance(entry, dict):
            survivors.append(entry)
            continue
        inner = entry.get("hooks")
        if not isinstance(inner, list):
            survivors.append(entry)
            continue
        kept_hooks = [hook for hook in inner if not _is_ours(hook)]
        if len(kept_hooks) == len(inner):
            # Nothing of ours in this entry — keep it verbatim.
            survivors.append(entry)
            continue
        if not kept_hooks:
            # Entry held only our hook(s) — drop the now-empty entry entirely.
            continue
        entry["hooks"] = kept_hooks
        survivors.append(entry)
    pre_tool_use[:] = survivors


def _is_ours(hook: Any) -> bool:
    """Whether ``hook`` is a super-harness PreToolUse hook (by stable marker)."""
    if not isinstance(hook, dict):
        return False
    command = hook.get("command")
    return isinstance(command, str) and _OURS_MARKER in command


def _hook_entry(command: str) -> dict[str, Any]:
    return {
        "matcher": _MATCHER,
        "hooks": [{"type": "command", "command": command, "timeout": _TIMEOUT}],
    }
