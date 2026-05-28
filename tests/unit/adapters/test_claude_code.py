"""Tests for the reference ClaudeCodeAdapter (adapter-architecture §3.5).

Covers the concrete Claude Code adapter contract:
- `detect` — feature-file check (`.claude/` is a dir).
- `install_hooks` — resolves the `super-harness-hook` binary via `shutil.which`,
  registers a PreToolUse hook into `.claude/settings.json` (merge, no `.sh`,
  no SessionStart — deferred to Phase 9), raises on a missing binary, idempotent.
- `inject_context` — delegates to `super-harness change resume <id>`, returns
  stdout, tolerates empty / non-zero results without crashing.
- `agents_md_subsection` — returns a marker-wrapped static markdown block.

`install_hooks` tests use real file I/O via `tmp_path` and monkeypatch only the
PATH-resolution (`shutil.which`) seam; `inject_context` monkeypatches
`subprocess.run` so the test never shells out to a real CLI.
"""

from __future__ import annotations

import glob
import json
import subprocess
from pathlib import Path

import pytest

from super_harness.adapters.agent.claude_code import ClaudeCodeAdapter

_RESOLVED = "/abs/bin/super-harness-hook"
_EXPECTED_COMMAND = f"{_RESOLVED} --agent claude-code"
_MATCHER = "Edit|Write|MultiEdit|NotebookEdit"

_CANONICAL_CAPABILITY_KEYS = {
    "pre_tool_use_hook",
    "post_tool_use_hook",
    "session_start_hook",
    "session_end_hook",
    "pre_commit_hook",
    "rules_file_injection",
    "mcp_server",
    "subprocess_execution",
}


def _commands(settings: dict[str, object]) -> list[str]:
    hooks = settings["hooks"]
    assert isinstance(hooks, dict)
    entries = hooks["PreToolUse"]
    assert isinstance(entries, list)
    out: list[str] = []
    for entry in entries:
        for hook in entry.get("hooks", []):  # type: ignore[union-attr]
            out.append(hook["command"])
    return out


def test_name_and_version() -> None:
    adapter = ClaudeCodeAdapter()
    assert adapter.name == "claude-code"
    assert adapter.version == "0.1.0"


def test_capabilities_match_spec() -> None:
    adapter = ClaudeCodeAdapter()
    assert set(adapter.capabilities) == _CANONICAL_CAPABILITY_KEYS
    assert adapter.capabilities == {
        "pre_tool_use_hook": True,
        "post_tool_use_hook": True,
        "session_start_hook": True,
        "session_end_hook": False,
        "pre_commit_hook": False,
        "rules_file_injection": True,
        "mcp_server": True,
        "subprocess_execution": True,
    }


def test_detect_false_without_claude_dir(tmp_path: Path) -> None:
    assert ClaudeCodeAdapter().detect(tmp_path) is False


def test_detect_true_with_claude_dir(tmp_path: Path) -> None:
    (tmp_path / ".claude").mkdir()
    assert ClaudeCodeAdapter().detect(tmp_path) is True


def test_install_hooks_writes_settings_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "super_harness.adapters.agent.claude_code.shutil.which",
        lambda _name: _RESOLVED,
    )
    ClaudeCodeAdapter().install_hooks(tmp_path)

    settings_path = tmp_path / ".claude" / "settings.json"
    assert settings_path.exists()
    settings = json.loads(settings_path.read_text())
    cmds = _commands(settings)
    assert cmds == [_EXPECTED_COMMAND]
    # No SessionStart wiring in v0.1 (deferred to Phase 9).
    assert "SessionStart" not in settings["hooks"]
    # The matcher comes from the merge util's canonical set.
    assert settings["hooks"]["PreToolUse"][0]["matcher"] == _MATCHER
    # No `.sh` script is written anywhere.
    assert glob.glob(str(tmp_path / "**" / "*.sh"), recursive=True) == []


def test_install_hooks_missing_binary_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "super_harness.adapters.agent.claude_code.shutil.which",
        lambda _name: None,
    )
    with pytest.raises(RuntimeError, match="super-harness-hook"):
        ClaudeCodeAdapter().install_hooks(tmp_path)


def test_install_hooks_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "super_harness.adapters.agent.claude_code.shutil.which",
        lambda _name: _RESOLVED,
    )
    adapter = ClaudeCodeAdapter()
    adapter.install_hooks(tmp_path)
    adapter.install_hooks(tmp_path)

    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    assert _commands(settings).count(_EXPECTED_COMMAND) == 1


def test_inject_context_returns_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="# change my-slug\n", stderr="")

    monkeypatch.setattr(
        "super_harness.adapters.agent.claude_code.subprocess.run", fake_run
    )
    out = ClaudeCodeAdapter().inject_context("my-slug")
    assert out == "# change my-slug\n"
    assert captured["cmd"] == [
        "super-harness",
        "change",
        "resume",
        "my-slug",
    ]


def test_inject_context_empty_result_returns_empty_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        # Non-zero exit + empty stdout (e.g. unknown slug) must not crash.
        return subprocess.CompletedProcess(cmd, 2, stdout="", stderr="boom")

    monkeypatch.setattr(
        "super_harness.adapters.agent.claude_code.subprocess.run", fake_run
    )
    assert ClaudeCodeAdapter().inject_context("nope") == ""


def test_agents_md_subsection_has_markers() -> None:
    block = ClaudeCodeAdapter().agents_md_subsection()
    assert isinstance(block, str)
    assert "<!-- super-harness agent: claude-code -->" in block
    assert "<!-- /super-harness agent: claude-code -->" in block
    # Mentions the gate behaviour + recovery path per spec §3.5.
    assert "PreToolUse" in block
    assert "super-harness status" in block


def test_on_uninstall_restores_latest_backup(tmp_path: Path) -> None:
    settings_path = tmp_path / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(json.dumps({"hooks": {"PreToolUse": ["mutated"]}}))
    original = {"model": "claude-opus", "hooks": {}}
    # Two backups; the newer (higher ts) must win.
    settings_path.with_name(
        "settings.json.super-harness-backup.100"
    ).write_text(json.dumps({"model": "stale"}))
    settings_path.with_name(
        "settings.json.super-harness-backup.200"
    ).write_text(json.dumps(original))

    ClaudeCodeAdapter().on_uninstall(tmp_path)

    assert json.loads(settings_path.read_text()) == original


def test_on_uninstall_no_backup_is_noop(tmp_path: Path) -> None:
    settings_path = tmp_path / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(json.dumps({"model": "keep"}))

    # No backup files present — best-effort uninstall must leave the file as-is.
    ClaudeCodeAdapter().on_uninstall(tmp_path)

    assert json.loads(settings_path.read_text()) == {"model": "keep"}
