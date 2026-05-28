"""Consolidated END-TO-END integration test for the Claude Code adapter (Task 9).

The reference-adapter ship guard: ONE realistic flow that runs the real
``super-harness init`` followed by ``super-harness adapter install claude-code``
against a workspace whose ``.claude/settings.json`` already holds user content,
then asserts that EVERYTHING lands together — both hooks, the settings backup,
and the AGENTS.md injection.

What this adds OVER the existing suites (deliberately NOT re-copied):

- ``tests/integration/cli/test_adapter.py`` (Task 7) covers the init → install
  AGENTS.md anchor consume / idempotent block / absent-skip / uninstall-restore
  round-trip — but it mocks ``shutil.which`` to a fake path and NEVER asserts
  ``settings.json`` in that flow, never pre-populates settings (so the backup
  path is unexercised at the integration layer), and never asserts BOTH hooks +
  the AGENTS.md block in a single flow.
- ``tests/unit/adapters/test_claude_code.py`` (Task 8) covers ``install_hooks``
  registering both hooks / rollback / pristine-backup restore — but at the
  ADAPTER unit level, not through the ``adapter install`` CLI, and not combined
  with ``init``'s AGENTS.md output.

The NET-NEW guard here: realistic pre-populated settings (exercises the
backup-on-prepopulated path), BOTH hooks resolved to the REAL on-PATH binaries,
the AGENTS.md anchor → real block in ONE CLI flow, and idempotency of all three.

⚠ This test SPAWNS the ``super-harness`` / ``super-harness-hook`` entry-point
binaries by NAME (it does NOT mock ``shutil.which``), so it requires the project
``.venv/bin`` to be on ``PATH`` — see the module skip below.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from click.testing import CliRunner

from super_harness.cli import main

# These binaries must resolve on PATH for the adapter's real install to register
# the absolute-path hook commands. If they don't (e.g. running pytest without
# `.venv/bin` on PATH), skip the whole module rather than fail spuriously.
_HOOK_BIN = shutil.which("super-harness-hook")
_CLI_BIN = shutil.which("super-harness")
pytestmark = pytest.mark.skipif(
    _HOOK_BIN is None or _CLI_BIN is None,
    reason="requires super-harness / super-harness-hook entry points on PATH "
    "(run with `.venv/bin` on PATH)",
)

# Resolved exact commands the adapter writes (path-from-PATH + stable suffix).
_EXPECTED_PRE_TOOL_USE = f"{_HOOK_BIN} --agent claude-code"
_EXPECTED_SESSION_START = f"{_CLI_BIN} change resume"

# AGENTS.md markers (must match engineering/agents_md + the adapter block).
_NO_AGENT_ANCHOR = "<!-- super-harness no-agent-adapter-installed -->"
_CLAUDE_BEGIN = "<!-- super-harness agent: claude-code -->"
_CLAUDE_END = "<!-- /super-harness agent: claude-code -->"
_AGENT_LITERAL = "[AGENT_SECTION_AUTO_INSERTED]"
_FRAMEWORK_LITERAL = "[FRAMEWORK_SECTION_AUTO_INSERTED]"

# A user's pre-existing, unrelated config so the backup-on-prepopulated path runs.
_USER_HOOK_COMMAND = "/usr/local/bin/my-own-linter --check"
_USER_SETTINGS = {
    "model": "claude-opus-4",  # unrelated top-level key — must survive untouched.
    "hooks": {
        "PreToolUse": [
            {
                "matcher": "Bash",
                "hooks": [
                    {"type": "command", "command": _USER_HOOK_COMMAND, "timeout": 5}
                ],
            }
        ]
    },
}


def _settings_path(ws: Path) -> Path:
    return ws / ".claude" / "settings.json"


def _agents_md(ws: Path) -> Path:
    return ws / "AGENTS.md"


def _hook_commands(ws: Path, event: str) -> list[str]:
    """Every hook command string registered under ``hooks.<event>``."""
    data = json.loads(_settings_path(ws).read_text())
    commands: list[str] = []
    for entry in data.get("hooks", {}).get(event, []):
        for hook in entry.get("hooks", []):
            commands.append(hook["command"])
    return commands


def _backups(ws: Path) -> list[Path]:
    parent = _settings_path(ws).parent
    return sorted(parent.glob("settings.json.super-harness-backup.*"))


def _prepopulate_settings(ws: Path) -> None:
    """Write a user's existing `.claude/settings.json` BEFORE init/install."""
    path = _settings_path(ws)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_USER_SETTINGS, indent=2) + "\n")


def _init(ws: Path):
    return CliRunner().invoke(main, ["--workspace", str(ws), "init"])


def _install(ws: Path):
    return CliRunner().invoke(
        main, ["--workspace", str(ws), "adapter", "install", "claude-code"]
    )


def _uninstall(ws: Path):
    return CliRunner().invoke(
        main,
        ["--workspace", str(ws), "--quiet", "adapter", "uninstall", "claude-code"],
    )


def test_init_then_install_lands_hooks_backup_and_agents_md(tmp_path: Path) -> None:
    """init → install claude-code: both hooks + a backup + the AGENTS.md block,
    with the user's pre-existing settings preserved — all in one realistic flow.

    This is the consolidated reference-adapter ship guard. It exercises the REAL
    entry-point binaries (no `shutil.which` mock) so the registered hook commands
    are the actual resolved absolute paths a user would get.
    """
    # 1. A workspace whose `.claude/settings.json` already holds user content.
    _prepopulate_settings(tmp_path)

    # 2. `super-harness init` — writes `.harness/` + the root AGENTS.md (outer
    #    section + plain framework block + the no-agent anchor).
    init_result = _init(tmp_path)
    assert init_result.exit_code == 0, init_result.output
    agents_after_init = _agents_md(tmp_path).read_text()
    assert _NO_AGENT_ANCHOR in agents_after_init
    assert "super-harness section begin" in agents_after_init
    assert "super-harness framework: plain" in agents_after_init
    # init consumes both *_SECTION_AUTO_INSERTED literals — none must remain.
    assert _AGENT_LITERAL not in agents_after_init
    assert _FRAMEWORK_LITERAL not in agents_after_init

    # 3. `super-harness adapter install claude-code`.
    install_result = _install(tmp_path)
    assert install_result.exit_code == 0, install_result.output

    # 4a. BOTH hooks registered with the resolved-binary commands.
    pre_tool_use = _hook_commands(tmp_path, "PreToolUse")
    session_start = _hook_commands(tmp_path, "SessionStart")
    assert _EXPECTED_PRE_TOOL_USE in pre_tool_use
    assert _EXPECTED_SESSION_START in session_start
    # The SessionStart entry carries no tool matcher (fires on all session sources).
    settings = json.loads(_settings_path(tmp_path).read_text())
    assert "matcher" not in settings["hooks"]["SessionStart"][0]

    # 4a (cont). The user's pre-existing hook + unrelated top-level key survive.
    assert _USER_HOOK_COMMAND in pre_tool_use
    assert settings["model"] == "claude-opus-4"

    # 4b. A backup was written because settings.json pre-existed (NET-NEW at the
    #     integration layer) — and it captures the pristine user file.
    backups = _backups(tmp_path)
    assert backups, "expected a settings.json.super-harness-backup.* on prepopulated install"
    earliest = json.loads(backups[0].read_text())
    assert earliest == _USER_SETTINGS, "earliest backup must be the pristine user settings"

    # 4c. AGENTS.md: the no-agent anchor is consumed by the real claude-code block;
    #     no [AGENT_SECTION_AUTO_INSERTED] literal anywhere; outer + framework kept.
    agents = _agents_md(tmp_path).read_text()
    assert _CLAUDE_BEGIN in agents
    assert _CLAUDE_END in agents
    assert _NO_AGENT_ANCHOR not in agents
    assert _AGENT_LITERAL not in agents
    assert "super-harness section begin" in agents
    assert "super-harness framework: plain" in agents

    # 4d. Idempotency: a SECOND install leaves exactly one of each.
    second = _install(tmp_path)
    assert second.exit_code == 0, second.output
    assert _hook_commands(tmp_path, "PreToolUse").count(_EXPECTED_PRE_TOOL_USE) == 1
    assert _hook_commands(tmp_path, "SessionStart").count(_EXPECTED_SESSION_START) == 1
    # The user's hook is still the only OTHER PreToolUse command (not duplicated).
    assert _hook_commands(tmp_path, "PreToolUse").count(_USER_HOOK_COMMAND) == 1
    agents_again = _agents_md(tmp_path).read_text()
    assert agents_again.count(_CLAUDE_BEGIN) == 1
    assert agents_again.count(_CLAUDE_END) == 1


def test_init_install_uninstall_restores_pristine_settings_and_anchor(
    tmp_path: Path,
) -> None:
    """init → install → uninstall returns settings.json to its pristine pre-install
    state AND restores the AGENTS.md no-agent anchor.

    Complements test_adapter.py's uninstall coverage (which mocks `which` and does
    not assert settings.json): here the FULL end-to-end with the real binaries and
    a pre-populated settings file proves uninstall removes BOTH our hooks (the
    earliest-backup restore) while leaving the user's content exactly as it was.
    """
    _prepopulate_settings(tmp_path)
    assert _init(tmp_path).exit_code == 0
    assert _install(tmp_path).exit_code == 0
    # Sanity: our hooks were registered before uninstall.
    assert _EXPECTED_PRE_TOOL_USE in _hook_commands(tmp_path, "PreToolUse")
    assert _EXPECTED_SESSION_START in _hook_commands(tmp_path, "SessionStart")

    uninstall_result = _uninstall(tmp_path)
    assert uninstall_result.exit_code == 0, uninstall_result.output

    # settings.json restored to the exact pristine user state (both hooks gone,
    # user hook + unrelated key intact).
    restored = json.loads(_settings_path(tmp_path).read_text())
    assert restored == _USER_SETTINGS

    # AGENTS.md: claude-code block removed, no-agent anchor restored, framework kept.
    agents = _agents_md(tmp_path).read_text()
    assert _CLAUDE_BEGIN not in agents
    assert _CLAUDE_END not in agents
    assert _NO_AGENT_ANCHOR in agents
    assert "super-harness framework: plain" in agents
