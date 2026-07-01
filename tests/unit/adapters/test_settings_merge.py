"""Tests for the safe `.claude/settings.json` PreToolUse hook merge utility.

Covers the four contract cases from adapter-architecture §3.5 install steps:
absent-file create, pre-existing merge + backup, idempotent dedupe, and
defensive handling of a corrupt user file. All tests use real file I/O via
`tmp_path` (no mocking) per the task brief.
"""

from __future__ import annotations

import glob
import json
from pathlib import Path

import pytest

from super_harness.adapters.agent._settings_merge import (
    merge_pre_tool_use_hook,
    merge_session_start_hook,
    merge_stop_hook,
)

_COMMAND = "/abs/bin/super-harness-hook --agent claude-code"
_MATCHER = "Edit|Write|MultiEdit|NotebookEdit"

# SessionStart command: the user-facing CLI (no slug → active change).
_SS_COMMAND = "/abs/bin/super-harness change resume"


def _pre_tool_use(settings: dict[str, object]) -> list[dict[str, object]]:
    hooks = settings["hooks"]
    assert isinstance(hooks, dict)
    entries = hooks["PreToolUse"]
    assert isinstance(entries, list)
    return entries


def _commands(entries: list[dict[str, object]]) -> list[str]:
    out: list[str] = []
    for entry in entries:
        for hook in entry.get("hooks", []):  # type: ignore[union-attr]
            out.append(hook["command"])
    return out


def test_absent_file_creates_settings_with_entry_and_no_backup(tmp_path: Path) -> None:
    settings_path = tmp_path / ".claude" / "settings.json"
    assert not settings_path.exists()

    merge_pre_tool_use_hook(settings_path, command=_COMMAND)

    assert settings_path.exists()
    settings = json.loads(settings_path.read_text())
    entries = _pre_tool_use(settings)
    assert len(entries) == 1
    assert entries[0]["matcher"] == _MATCHER
    assert _COMMAND in _commands(entries)

    # Nothing pre-existed, so no backup should have been written.
    backups = glob.glob(str(settings_path) + ".super-harness-backup.*")
    assert backups == []


def test_pre_existing_file_merges_and_backs_up(tmp_path: Path) -> None:
    settings_path = tmp_path / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    original = {
        "model": "claude-opus",
        "permissions": {"allow": ["Bash(ls:*)"]},
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [
                        {"type": "command", "command": "user-bash-guard"},
                    ],
                }
            ],
            "PostToolUse": [
                {"matcher": "Write", "hooks": [{"type": "command", "command": "x"}]}
            ],
        },
    }
    settings_path.write_text(json.dumps(original, indent=2))

    merge_pre_tool_use_hook(settings_path, command=_COMMAND)

    settings = json.loads(settings_path.read_text())
    # User top-level keys preserved.
    assert settings["model"] == "claude-opus"
    assert settings["permissions"] == {"allow": ["Bash(ls:*)"]}
    # Other hook event types preserved.
    assert settings["hooks"]["PostToolUse"] == original["hooks"]["PostToolUse"]
    # User's Bash PreToolUse hook preserved, plus our entry appended.
    cmds = _commands(_pre_tool_use(settings))
    assert "user-bash-guard" in cmds
    assert _COMMAND in cmds

    backups = glob.glob(str(settings_path) + ".super-harness-backup.*")
    assert len(backups) == 1
    backed = json.loads(Path(backups[0]).read_text())
    assert backed == original


def test_idempotent_no_duplicate_entry(tmp_path: Path) -> None:
    settings_path = tmp_path / ".claude" / "settings.json"

    merge_pre_tool_use_hook(settings_path, command=_COMMAND)
    merge_pre_tool_use_hook(settings_path, command=_COMMAND)

    settings = json.loads(settings_path.read_text())
    cmds = _commands(_pre_tool_use(settings))
    assert cmds.count(_COMMAND) == 1


def test_corrupt_hooks_not_a_dict_raises_value_error(tmp_path: Path) -> None:
    settings_path = tmp_path / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(json.dumps({"hooks": "not-a-dict"}))

    with pytest.raises(ValueError, match="hooks"):
        merge_pre_tool_use_hook(settings_path, command=_COMMAND)


def test_corrupt_pre_tool_use_not_a_list_raises_value_error(tmp_path: Path) -> None:
    settings_path = tmp_path / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(json.dumps({"hooks": {"PreToolUse": {"oops": 1}}}))

    with pytest.raises(ValueError, match="PreToolUse"):
        merge_pre_tool_use_hook(settings_path, command=_COMMAND)


_OLD_COMMAND = "/old/bin/super-harness-hook --agent claude-code"
_NEW_COMMAND = "/new/bin/super-harness-hook --agent claude-code"


def test_binary_path_change_replaces_stale_entry(tmp_path: Path) -> None:
    """A relocated binary (new command path) must REPLACE our prior entry, not
    accumulate a second one — dedupe is by the stable `--agent claude-code`
    marker, not the full path. Unrelated user hooks survive untouched."""
    settings_path = tmp_path / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    original = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [{"type": "command", "command": "user-bash-guard"}],
                }
            ]
        }
    }
    settings_path.write_text(json.dumps(original, indent=2))

    merge_pre_tool_use_hook(settings_path, command=_OLD_COMMAND)
    merge_pre_tool_use_hook(settings_path, command=_NEW_COMMAND)

    settings = json.loads(settings_path.read_text())
    entries = _pre_tool_use(settings)
    # Exactly one super-harness entry, pointing at the NEW command.
    sh_cmds = [c for c in _commands(entries) if "--agent claude-code" in c]
    assert sh_cmds == [_NEW_COMMAND]
    # The user's unrelated Bash hook is still present.
    assert "user-bash-guard" in _commands(entries)


def test_idempotent_reinstall_writes_no_new_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pre-existing file already containing our exact entry: a repeat install
    with the same command must change nothing — no new backup, byte-identical.

    Time is advanced between the two installs so that a backup written by the
    SECOND call would get a distinct filename — making an erroneous extra backup
    unambiguously detectable regardless of wall-clock timing.
    """
    settings_path = tmp_path / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    # Seed the file already containing our entry (first install on an existing
    # file would create one backup; we want to isolate the SECOND call).
    settings_path.write_text(json.dumps({"other": "key"}, indent=2))

    clock = iter([1000, 2000, 3000, 4000])
    monkeypatch.setattr(
        "super_harness.adapters.agent._settings_merge.time.time",
        lambda: next(clock),
    )
    monkeypatch.setattr(
        "super_harness.adapters.agent._settings_merge.time.time_ns",
        lambda: next(clock),
    )

    merge_pre_tool_use_hook(settings_path, command=_COMMAND)

    pattern = str(settings_path) + ".super-harness-backup.*"
    backups_before = sorted(glob.glob(pattern))
    bytes_before = settings_path.read_bytes()

    merge_pre_tool_use_hook(settings_path, command=_COMMAND)

    backups_after = sorted(glob.glob(pattern))
    assert backups_after == backups_before  # no new backup written
    assert settings_path.read_bytes() == bytes_before  # byte-identical


def test_pristine_backup_preserved_across_changes(tmp_path: Path) -> None:
    """The backup written on a real change must capture the PRISTINE file — it
    must not contain our injected hook. (ns-naming + only-on-change protect it.)"""
    settings_path = tmp_path / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    pristine = {
        "model": "claude-opus",
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [{"type": "command", "command": "user-bash-guard"}],
                }
            ]
        },
    }
    settings_path.write_text(json.dumps(pristine, indent=2))

    merge_pre_tool_use_hook(settings_path, command=_COMMAND)

    backups = glob.glob(str(settings_path) + ".super-harness-backup.*")
    assert len(backups) == 1
    backed = json.loads(Path(backups[0]).read_text())
    # The backup is the pristine file — no super-harness entry leaked into it.
    assert backed == pristine
    backed_cmds = _commands(_pre_tool_use(backed))
    assert all("--agent claude-code" not in c for c in backed_cmds)


# -------------------- SessionStart merge (Task 8) --------------------
#
# Symmetric with the PreToolUse merge: backup-on-change, replace-not-accumulate
# (idempotent), collision-proof backup naming, preserve unrelated config. The
# SessionStart entry shape drops the tool matcher (per Claude Code's
# SessionStart schema — matcher is a session-source matcher, optional; omitting
# it fires on all session starts). "Ours" is identified by the `change resume`
# command substring.


def _session_start(settings: dict[str, object]) -> list[dict[str, object]]:
    hooks = settings["hooks"]
    assert isinstance(hooks, dict)
    entries = hooks["SessionStart"]
    assert isinstance(entries, list)
    return entries


def test_session_start_absent_file_creates_entry_no_matcher(tmp_path: Path) -> None:
    settings_path = tmp_path / ".claude" / "settings.json"
    assert not settings_path.exists()

    merge_session_start_hook(settings_path, command=_SS_COMMAND)

    settings = json.loads(settings_path.read_text())
    entries = _session_start(settings)
    assert len(entries) == 1
    # No tool matcher on SessionStart (fires on all session sources).
    assert "matcher" not in entries[0]
    assert _SS_COMMAND in _commands(entries)

    backups = glob.glob(str(settings_path) + ".super-harness-backup.*")
    assert backups == []


def test_session_start_preserves_unrelated_hooks(tmp_path: Path) -> None:
    """Preserve other hook types AND an unrelated user SessionStart hook."""
    settings_path = tmp_path / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    user_session_start = {
        "matcher": "startup",
        "hooks": [{"type": "command", "command": "user-greet.sh"}],
    }
    original = {
        "model": "claude-opus",
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [{"type": "command", "command": "user-bash-guard"}],
                }
            ],
            "SessionStart": [user_session_start],
        },
    }
    settings_path.write_text(json.dumps(original, indent=2))

    merge_session_start_hook(settings_path, command=_SS_COMMAND)

    settings = json.loads(settings_path.read_text())
    # Top-level + other hook types untouched.
    assert settings["model"] == "claude-opus"
    assert settings["hooks"]["PreToolUse"] == original["hooks"]["PreToolUse"]
    # The user's unrelated SessionStart hook survives, ours is appended.
    cmds = _commands(_session_start(settings))
    assert "user-greet.sh" in cmds
    assert _SS_COMMAND in cmds

    backups = glob.glob(str(settings_path) + ".super-harness-backup.*")
    assert len(backups) == 1
    assert json.loads(Path(backups[0]).read_text()) == original


def test_session_start_idempotent_no_duplicate(tmp_path: Path) -> None:
    settings_path = tmp_path / ".claude" / "settings.json"

    merge_session_start_hook(settings_path, command=_SS_COMMAND)
    merge_session_start_hook(settings_path, command=_SS_COMMAND)

    settings = json.loads(settings_path.read_text())
    cmds = _commands(_session_start(settings))
    assert cmds.count(_SS_COMMAND) == 1


def test_session_start_binary_relocation_replaces_stale_entry(tmp_path: Path) -> None:
    """A relocated binary REPLACES our prior SessionStart entry (dedupe by the
    `change resume` marker, not the full path) — unrelated user hooks survive."""
    old = "/old/bin/super-harness change resume"
    new = "/new/bin/super-harness change resume"
    settings_path = tmp_path / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    original = {
        "hooks": {
            "SessionStart": [
                {
                    "matcher": "startup",
                    "hooks": [{"type": "command", "command": "user-greet.sh"}],
                }
            ]
        }
    }
    settings_path.write_text(json.dumps(original, indent=2))

    merge_session_start_hook(settings_path, command=old)
    merge_session_start_hook(settings_path, command=new)

    settings = json.loads(settings_path.read_text())
    cmds = _commands(_session_start(settings))
    sh_cmds = [c for c in cmds if "change resume" in c]
    assert sh_cmds == [new]
    assert "user-greet.sh" in cmds


def test_session_start_corrupt_not_a_list_raises(tmp_path: Path) -> None:
    settings_path = tmp_path / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(json.dumps({"hooks": {"SessionStart": {"oops": 1}}}))

    with pytest.raises(ValueError, match="SessionStart"):
        merge_session_start_hook(settings_path, command=_SS_COMMAND)


def test_session_start_idempotent_reinstall_writes_no_new_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Repeat install with the same command changes nothing — no new backup."""
    settings_path = tmp_path / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(json.dumps({"other": "key"}, indent=2))

    clock = iter([1000, 2000, 3000, 4000])
    monkeypatch.setattr(
        "super_harness.adapters.agent._settings_merge.time.time_ns",
        lambda: next(clock),
    )

    merge_session_start_hook(settings_path, command=_SS_COMMAND)

    pattern = str(settings_path) + ".super-harness-backup.*"
    backups_before = sorted(glob.glob(pattern))
    bytes_before = settings_path.read_bytes()

    merge_session_start_hook(settings_path, command=_SS_COMMAND)

    assert sorted(glob.glob(pattern)) == backups_before
    assert settings_path.read_bytes() == bytes_before


def test_merge_pre_tool_use_respects_custom_matcher_and_marker(tmp_path):
    import json

    from super_harness.adapters.agent._settings_merge import merge_pre_tool_use_hook

    p = tmp_path / "hooks.json"
    merge_pre_tool_use_hook(
        p,
        command="/abs/super-harness-hook --agent codex",
        matcher="^(apply_patch|Edit|Write)$",
        marker="--agent codex",
    )
    data = json.loads(p.read_text())
    entry = data["hooks"]["PreToolUse"][0]
    assert entry["matcher"] == "^(apply_patch|Edit|Write)$"
    assert entry["hooks"][0]["command"] == "/abs/super-harness-hook --agent codex"


def test_codex_marker_does_not_strip_claude_pre_tool_use(tmp_path):
    """A codex re-merge must not remove a co-resident claude-code entry."""
    import json

    from super_harness.adapters.agent._settings_merge import merge_pre_tool_use_hook

    p = tmp_path / "hooks.json"
    # Pre-seed a claude-code entry (foreign marker).
    p.write_text(json.dumps({"hooks": {"PreToolUse": [
        {"matcher": "Edit", "hooks": [
            {"type": "command", "command": "/x super-harness-hook --agent claude-code"}]}
    ]}}))
    merge_pre_tool_use_hook(
        p, command="/abs/h --agent codex",
        matcher="^(apply_patch|Edit|Write)$", marker="--agent codex",
    )
    cmds = [h["command"] for e in json.loads(p.read_text())["hooks"]["PreToolUse"]
            for h in e["hooks"]]
    assert any("--agent claude-code" in c for c in cmds)  # foreign preserved
    assert any("--agent codex" in c for c in cmds)        # ours added


def test_merge_stop_adds_entry(tmp_path):
    hooks = tmp_path / "settings.json"
    merge_stop_hook(hooks, command="/abs/super-harness-hook --agent claude-code --event stop")
    data = json.loads(hooks.read_text())
    entries = data["hooks"]["Stop"]
    assert any("--event stop" in h["command"] for e in entries for h in e["hooks"])
    assert all("matcher" not in e for e in entries)


def test_merge_stop_preserves_existing_hooks(tmp_path):
    hooks = tmp_path / "settings.json"
    hooks.write_text(json.dumps({"hooks": {"PreToolUse": [
        {"matcher": "Edit", "hooks": [{"type": "command", "command": "keepme"}]}]}}))
    merge_stop_hook(hooks, command="/abs/super-harness-hook --agent claude-code --event stop")
    data = json.loads(hooks.read_text())
    assert data["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == "keepme"
    assert "Stop" in data["hooks"]


def test_merge_stop_idempotent(tmp_path):
    hooks = tmp_path / "settings.json"
    cmd = "/abs/super-harness-hook --agent claude-code --event stop"
    merge_stop_hook(hooks, command=cmd)
    first = hooks.read_text()
    merge_stop_hook(hooks, command=cmd)
    assert hooks.read_text() == first
