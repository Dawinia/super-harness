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

from super_harness.adapters.agent._settings_merge import merge_pre_tool_use_hook

_COMMAND = "/abs/bin/super-harness-hook --agent claude-code"
_MATCHER = "Edit|Write|MultiEdit|NotebookEdit"


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
