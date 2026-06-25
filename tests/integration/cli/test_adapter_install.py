"""Integration tests for `super-harness adapter install claude-code` (Task 5.7).

Wires the v0.1 MINIMAL adapter CLI: only `adapter install claude-code`, which
registers the PreToolUse hook into `.claude/settings.local.json` via the
`ClaudeCodeAdapter` (Task 5.6). No uninstall/list, no other adapters, no
adapters.yaml persistence (all Phase 6).

`shutil.which` is monkeypatched so the real `super-harness-hook` binary need not
be installed on PATH for the happy-path tests; the binary's resolved absolute
path lands verbatim in the registered hook command (`<abs> --agent claude-code`).

Coverage map:
- test_install_creates_pre_tool_use_hook  — fresh `.harness/` workspace, no
                                            `.claude/` → exit 0, settings.local.json
                                            created with the gate hook command
- test_install_is_idempotent              — second run → exit 0, still exactly
                                            one entry (merge dedupe)
- test_install_binary_not_on_path_exits_1 — which→None → exit 1 (EXIT_GENERIC),
                                            clear message, NOT a stack trace
- test_install_no_harness_exits_3         — no `.harness/` → EXIT_NO_CONFIG (3)
"""
import json
import shutil
from pathlib import Path

import pytest
from click.testing import CliRunner

from super_harness.cli import main

_FAKE_HOOK = "/usr/local/bin/super-harness-hook"
_EXPECTED_COMMAND = f"{_FAKE_HOOK} --agent claude-code"


def _settings(ws: Path) -> Path:
    return ws / ".claude" / "settings.local.json"


def _pre_tool_use_commands(ws: Path) -> list[str]:
    """Collect every PreToolUse hook command string from settings.local.json."""
    data = json.loads(_settings(ws).read_text())
    commands: list[str] = []
    for entry in data["hooks"]["PreToolUse"]:
        for hook in entry["hooks"]:
            commands.append(hook["command"])
    return commands


def test_install_creates_pre_tool_use_hook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fresh `.harness/` workspace (no `.claude/`) → exit 0, hook registered.

    Documents the `.claude/`-absent decision: install works in a repo without a
    pre-existing `.claude/` dir; `merge_pre_tool_use_hook` mkdirs parents and
    creates settings.local.json from empty config.
    """
    (tmp_path / ".harness").mkdir()
    monkeypatch.setattr(shutil, "which", lambda _name: _FAKE_HOOK)
    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "adapter", "install", "claude-code"],
    )

    assert r.exit_code == 0, r.output
    assert _settings(tmp_path).exists()
    assert _pre_tool_use_commands(tmp_path) == [_EXPECTED_COMMAND]


def test_install_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Second run → exit 0 and still exactly one entry (leans on merge dedupe)."""
    (tmp_path / ".harness").mkdir()
    monkeypatch.setattr(shutil, "which", lambda _name: _FAKE_HOOK)
    first = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "adapter", "install", "claude-code"]
    )
    second = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "adapter", "install", "claude-code"]
    )

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert _pre_tool_use_commands(tmp_path) == [_EXPECTED_COMMAND]


def test_install_binary_not_on_path_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`which` → None → EXIT_GENERIC (1) with a clear message, not a traceback."""
    (tmp_path / ".harness").mkdir()
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "adapter", "install", "claude-code"],
    )

    assert r.exit_code == 1
    assert "super-harness adapter install:" in r.stderr
    assert "not found on PATH" in r.stderr
    assert "Traceback" not in r.stderr
    # No settings file should have been written on failure.
    assert not _settings(tmp_path).exists()


def test_install_no_harness_exits_3(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No `.harness/` → HarnessNotInitialized → EXIT_NO_CONFIG (3)."""
    monkeypatch.setattr(shutil, "which", lambda _name: _FAKE_HOOK)
    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "adapter", "install", "claude-code"],
    )

    assert r.exit_code == 3
    assert "super-harness adapter install:" in r.stderr
    assert "Hint:" in r.stderr


def test_install_message_is_adapter_driven_not_claude_hardcoded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex install announces .codex/hooks.json, never .claude/ paths."""
    (tmp_path / ".harness").mkdir()
    (tmp_path / ".codex").mkdir()  # codex adapter detect()s on .codex/
    monkeypatch.setattr(shutil, "which", lambda _name: f"/usr/local/bin/{_name}")
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "adapter", "install", "codex"],
    )
    assert r.exit_code == 0, r.output
    assert ".codex/hooks.json" in r.output
    assert ".claude/" not in r.output
