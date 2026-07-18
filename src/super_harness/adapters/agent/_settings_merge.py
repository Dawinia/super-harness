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
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "SettingsMergePlan",
    "SettingsUpdateInProgressError",
    "StaleSettingsPlanError",
    "apply_settings_merge_plan",
    "merge_pre_tool_use_hook",
    "merge_session_start_hook",
    "merge_stop_hook",
    "plan_settings_merge",
    "restore_or_remove_managed_hooks",
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

# Stable marker for our Stop hook command. The Stop command is
# `<abs super-harness-hook> --agent claude-code --event stop`; the full
# `--agent claude-code --event stop` flag-pair is the path-independent substring
# identifying it as ours. Deliberately the WHOLE pair (not the bare `--event stop`) so
# an unrelated user Stop hook that merely happens to pass `--event stop` to some other
# tool is never stripped as if it were ours.
_STOP_OURS_MARKER = "--agent claude-code --event stop"
# Per-hook timeout (seconds) for the Stop authoring-check command; matches the
# PreToolUse/SessionStart budget shape and is the OUTER bound the inner check must beat.
_STOP_TIMEOUT = 10


class StaleSettingsPlanError(RuntimeError):
    """The settings file no longer matches the bytes captured for review."""


class SettingsUpdateInProgressError(RuntimeError):
    """Another super-harness writer owns the settings transaction lock."""


@dataclass(frozen=True)
class SettingsMergePlan:
    path: Path
    original_bytes: bytes | None
    desired_bytes: bytes
    changed: bool
    backup_required: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", Path(self.path))


def plan_settings_merge(
    settings_path: Path,
    *,
    pre_tool_use_command: str,
    session_start_command: str,
    stop_command: str,
    pre_tool_use_matcher: str = _MATCHER,
    pre_tool_use_marker: str = _OURS_MARKER,
    session_start_marker: str = _SESSION_OURS_MARKER,
    stop_marker: str = _STOP_OURS_MARKER,
) -> SettingsMergePlan:
    """Purely compute the complete three-hook settings transaction."""
    original_bytes = settings_path.read_bytes() if settings_path.exists() else None
    original = (
        _parse_settings_bytes(settings_path, original_bytes)
        if original_bytes is not None
        else {}
    )
    settings = copy.deepcopy(original)
    hooks = _ensure_hooks_dict(settings)

    pre_tool_use = _ensure_event_list(hooks, "PreToolUse")
    _strip_entries(pre_tool_use, pre_tool_use_marker)
    pre_tool_use.append(_hook_entry(pre_tool_use_command, pre_tool_use_matcher))

    session_start = _ensure_event_list(hooks, "SessionStart")
    _strip_entries(session_start, session_start_marker)
    session_start.append(_session_start_entry(session_start_command))

    stop = _ensure_event_list(hooks, "Stop")
    _strip_entries(stop, stop_marker)
    stop.append(_stop_entry(stop_command))

    changed = settings != original
    desired_bytes = (
        json.dumps(settings, indent=2).encode("utf-8") + b"\n"
        if changed
        else original_bytes or b""
    )
    return SettingsMergePlan(
        path=settings_path,
        original_bytes=original_bytes,
        desired_bytes=desired_bytes,
        changed=changed,
        backup_required=changed and original_bytes is not None,
    )


def apply_settings_merge_plan(plan: SettingsMergePlan) -> None:
    """Apply a reviewed plan under an exclusive sibling lock and atomic replace."""
    plan.path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = plan.path.with_name(f"{plan.path.name}.super-harness.lock")
    _acquire_settings_lock(lock_path)
    temp_path: Path | None = None
    try:
        current = plan.path.read_bytes() if plan.path.exists() else None
        if current != plan.original_bytes:
            raise StaleSettingsPlanError(
                f"{plan.path} changed after review; rerun init and review the new plan."
            )
        if not plan.changed:
            return
        if plan.backup_required:
            _write_backup_bytes(plan.path, plan.original_bytes or b"")

        temp_fd, temp_name = tempfile.mkstemp(
            prefix=f".{plan.path.name}.super-harness.",
            suffix=".tmp",
            dir=plan.path.parent,
        )
        temp_path = Path(temp_name)
        try:
            _write_temp_bytes(temp_fd, plan.desired_bytes)
        finally:
            os.close(temp_fd)
        os.replace(temp_path, plan.path)
        temp_path = None
    finally:
        try:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
        finally:
            lock_path.unlink(missing_ok=True)


def restore_or_remove_managed_hooks(
    settings_path: Path,
    *,
    pre_tool_use_marker: str,
    session_start_marker: str = _SESSION_OURS_MARKER,
    stop_marker: str,
) -> None:
    """Restore the pristine backup, or remove only marker-owned hook entries."""
    backups = sorted(
        settings_path.parent.glob(f"{settings_path.name}.super-harness-backup.*"),
        key=_backup_sort_key,
    )
    if backups:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_bytes(backups[0].read_bytes())
        return
    if not settings_path.exists():
        return

    original_bytes = settings_path.read_bytes()
    settings = _parse_settings_bytes(settings_path, original_bytes)
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return
    changed = False
    for event, marker in (
        ("PreToolUse", pre_tool_use_marker),
        ("SessionStart", session_start_marker),
        ("Stop", stop_marker),
    ):
        entries = hooks.get(event)
        if not isinstance(entries, list):
            continue
        before = copy.deepcopy(entries)
        _strip_entries(entries, marker)
        if entries != before:
            changed = True
        if not entries:
            hooks.pop(event, None)
    if not changed:
        return
    if not hooks:
        settings.pop("hooks", None)
    if not settings:
        settings_path.unlink()
        return
    settings_path.write_bytes(json.dumps(settings, indent=2).encode("utf-8") + b"\n")


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

    settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")


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

    settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")


def merge_stop_hook(
    settings_path: Path,
    *,
    command: str,
    marker: str = _STOP_OURS_MARKER,
) -> None:
    """Register super-harness's Stop hook in ``settings_path``, safely.

    Mirrors :func:`merge_session_start_hook` exactly (Stop hooks, like SessionStart,
    take NO ``matcher`` — they always fire on every turn end): computes the desired
    settings with any prior super-harness Stop entry replaced by a fresh one for
    ``command``, and writes only if it differs from disk. On a real change to a
    pre-existing file the original is backed up FIRST; an absent file starts from
    ``{}`` and writes no backup. "Ours" is the path-independent ``--event stop``
    substring, so a relocated binary replaces the stale entry instead of duplicating.

    Raises:
        ValueError: if the file is not valid JSON, is not a JSON object, or has a
            malformed ``hooks`` / ``hooks.Stop`` shape the user must fix.
    """
    existed = settings_path.exists()
    original = _read_settings(settings_path) if existed else {}

    settings = copy.deepcopy(original)
    hooks = _ensure_hooks_dict(settings)
    stop = _ensure_event_list(hooks, "Stop")

    _strip_entries(stop, marker)
    stop.append(_stop_entry(command))

    if settings == original:
        return

    if existed:
        _write_backup(settings_path)
    else:
        settings_path.parent.mkdir(parents=True, exist_ok=True)

    settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")


def _read_settings(settings_path: Path) -> dict[str, Any]:
    return _parse_settings_bytes(settings_path, settings_path.read_bytes())


def _parse_settings_bytes(settings_path: Path, raw: bytes) -> dict[str, Any]:
    try:
        parsed: Any = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
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
    _write_backup_bytes(settings_path, settings_path.read_bytes())


def _write_backup_bytes(settings_path: Path, content: bytes) -> Path:
    stamp = time.time_ns()
    while True:
        backup = settings_path.with_name(
            f"{settings_path.name}.super-harness-backup.{stamp}"
        )
        try:
            fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            stamp += 1
            continue
        try:
            _write_fd_bytes(fd, content)
        except BaseException:
            try:
                os.close(fd)
            except OSError:
                pass
            backup.unlink(missing_ok=True)
            raise
        try:
            os.close(fd)
        except BaseException:
            backup.unlink(missing_ok=True)
            raise
        return backup


def _acquire_settings_lock(lock_path: Path) -> None:
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise SettingsUpdateInProgressError(
            f"another settings update is in progress for {lock_path.name}"
        ) from exc
    try:
        _write_fd_bytes(fd, str(os.getpid()).encode("ascii"))
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        lock_path.unlink(missing_ok=True)
        raise
    try:
        os.close(fd)
    except BaseException:
        lock_path.unlink(missing_ok=True)
        raise


def _write_temp_bytes(fd: int, content: bytes) -> None:
    _write_fd_bytes(fd, content)


def _write_fd_bytes(fd: int, content: bytes) -> None:
    remaining = memoryview(content)
    while remaining:
        written = os.write(fd, remaining)
        if written == 0:
            raise OSError("could not complete settings write")
        remaining = remaining[written:]
    os.fsync(fd)


def _backup_sort_key(path: Path) -> int:
    try:
        return int(path.name.rsplit(".", 1)[-1])
    except ValueError:
        return -1


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


def _stop_entry(command: str) -> dict[str, Any]:
    # No ``matcher``: Claude Code Stop hooks do not support matchers and always fire
    # on turn end (Claude Code hooks schema, confirmed by the 2026-07-01 LIVE stub).
    return {
        "hooks": [{"type": "command", "command": command, "timeout": _STOP_TIMEOUT}],
    }
