"""Safe `.claude/settings.json` merge for super-harness's Claude Code hooks.

Registers super-harness's PreToolUse and SessionStart hook entries into a Claude
Code `settings.json` WITHOUT clobbering the user's existing config — merge, not
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
    "merge_session_start_hook",
]

# The PreToolUse matcher super-harness registers (sensor-gate §3.2.1). Covers
# every file-mutating tool Claude Code exposes.
_MATCHER = "Edit|Write|MultiEdit|NotebookEdit"
# Per-hook timeout (seconds) for the gate command, per the hook entry shape.
_TIMEOUT = 10
# Stable marker that identifies a PreToolUse hook command as super-harness's,
# independent of the (install-location-dependent) binary path. Used to
# dedupe/replace our own prior entries across binary relocations.
_OURS_MARKER = "--agent claude-code"

# Stable marker for our SessionStart hook command. The SessionStart command is
# `<abs super-harness> change resume` (no slug → active change); `change resume`
# is the path-independent substring that identifies it as ours, mirroring how
# `_OURS_MARKER` identifies the PreToolUse hook. Chosen over the bare binary
# name so an unrelated user hook that merely shells out to `super-harness` (e.g.
# `super-harness status`) is NOT mistaken for ours.
_SESSION_OURS_MARKER = "change resume"
# Per-hook timeout (seconds) for the SessionStart context-injection command.
# `change resume` reads events.jsonl + renders Markdown; 10s is generous for
# v0.1 log sizes and matches the PreToolUse budget shape.
_SESSION_TIMEOUT = 10


def merge_pre_tool_use_hook(
    settings_path: Path,
    *,
    command: str,
    matcher: str = _MATCHER,
    marker: str = _OURS_MARKER,
) -> None:
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

    _strip_entries(pre_tool_use, marker)
    pre_tool_use.append(_hook_entry(command, matcher))

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


def merge_session_start_hook(
    settings_path: Path,
    *,
    command: str,
    marker: str = _SESSION_OURS_MARKER,
) -> None:
    """Register super-harness's SessionStart hook in ``settings_path``, safely.

    Symmetric with :func:`merge_pre_tool_use_hook`: computes the desired settings
    (existing config with any prior super-harness SessionStart entry replaced by
    a fresh one for ``command``) and writes it only if it differs from disk — a
    true no-op re-install touches nothing. On a real change to a pre-existing
    file, the original is backed up FIRST to a collision-proof
    ``settings.json.super-harness-backup.<time_ns>`` path. An absent file starts
    from ``{}`` (creating the parent directory) and writes no backup.

    Claude Code's SessionStart schema differs from PreToolUse: its ``matcher`` is
    a *session-source* matcher (``startup`` / ``resume`` / ``clear`` /
    ``compact``), and omitting it fires on ALL session starts — which is exactly
    what we want for context injection. So our entry carries NO ``matcher`` (vs
    PreToolUse's tool matcher); the inner ``hooks`` array shape is identical
    (``{"type": "command", "command": ..., "timeout": ...}``).

    "Ours" is identified by the path-independent ``change resume`` substring, so
    a relocated binary REPLACES the stale entry instead of accumulating a
    duplicate, while an unrelated user SessionStart hook is left untouched.

    Raises:
        ValueError: if the file is not valid JSON, is not a JSON object, or has
            a malformed ``hooks`` / ``hooks.SessionStart`` shape the user must
            fix.
    """
    existed = settings_path.exists()
    original = _read_settings(settings_path) if existed else {}

    settings = copy.deepcopy(original)
    hooks = _ensure_hooks_dict(settings)
    session_start = _ensure_session_start_list(hooks)

    _strip_entries(session_start, marker)
    session_start.append(_session_start_entry(command))

    if settings == original:
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
    return _ensure_event_list(hooks, "PreToolUse")


def _ensure_session_start_list(hooks: dict[str, Any]) -> list[Any]:
    return _ensure_event_list(hooks, "SessionStart")


def _ensure_event_list(hooks: dict[str, Any], event: str) -> list[Any]:
    entries = hooks.setdefault(event, [])
    if not isinstance(entries, list):
        raise ValueError(
            f'"hooks.{event}" must be a JSON array, got '
            f"{type(entries).__name__}; the settings file looks corrupt — "
            f"fix it before installing the hook."
        )
    return entries


def _strip_entries(entries: list[Any], marker: str) -> None:
    """Remove hooks whose ``command`` contains ``marker``, in place.

    For each entry, drop any hook command containing ``marker`` (regardless of
    binary path). If stripping our hook(s) leaves an entry with an empty
    ``hooks`` list, drop the whole entry. Entries with no match (and any entry
    with surviving hooks) are kept untouched, preserving their order.
    """
    survivors: list[Any] = []
    for entry in entries:
        if not isinstance(entry, dict):
            survivors.append(entry)
            continue
        inner = entry.get("hooks")
        if not isinstance(inner, list):
            survivors.append(entry)
            continue
        kept_hooks = [hook for hook in inner if not _command_contains(hook, marker)]
        if len(kept_hooks) == len(inner):
            # Nothing of ours in this entry — keep it verbatim.
            survivors.append(entry)
            continue
        if not kept_hooks:
            # Entry held only our hook(s) — drop the now-empty entry entirely.
            continue
        entry["hooks"] = kept_hooks
        survivors.append(entry)
    entries[:] = survivors


def _command_contains(hook: Any, marker: str) -> bool:
    """Whether ``hook`` is a dict whose ``command`` string contains ``marker``."""
    if not isinstance(hook, dict):
        return False
    command = hook.get("command")
    return isinstance(command, str) and marker in command


def _hook_entry(command: str, matcher: str = _MATCHER) -> dict[str, Any]:
    return {
        "matcher": matcher,
        "hooks": [{"type": "command", "command": command, "timeout": _TIMEOUT}],
    }


def _session_start_entry(command: str) -> dict[str, Any]:
    # No tool ``matcher``: SessionStart's matcher is a session-source matcher
    # (startup/resume/clear/compact); omitting it fires on all session starts,
    # which is what we want for context injection (Claude Code hooks schema).
    return {
        "hooks": [{"type": "command", "command": command, "timeout": _SESSION_TIMEOUT}],
    }
