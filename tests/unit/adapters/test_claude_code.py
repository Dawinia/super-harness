"""Tests for the reference ClaudeCodeAdapter (adapter-architecture §3.5).

Covers the concrete Claude Code adapter contract:
- `detect` — feature-file check (`.claude/` is a dir).
- `install_hooks` — resolves BOTH the `super-harness-hook` (PreToolUse) and
  `super-harness` (SessionStart) binaries via `shutil.which`, registers a
  PreToolUse + a SessionStart hook into `.claude/settings.local.json` (merge,
  no `.sh`), raises on a missing binary BEFORE any write, idempotent, and rolls
  back the settings.local.json snapshot if a merge fails mid-install.
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

# `super-harness` (the CLI binary) resolves to a distinct abs path; the
# SessionStart hook invokes `<abs super-harness> change resume` (no slug).
_RESOLVED_CLI = "/abs/bin/super-harness"
_EXPECTED_SESSION_COMMAND = f"{_RESOLVED_CLI} change resume"


def _which_both(name: str) -> str | None:
    """Resolve both binaries install_hooks needs (PreToolUse + SessionStart)."""
    return {
        "super-harness-hook": _RESOLVED,
        "super-harness": _RESOLVED_CLI,
    }.get(name)


def _session_commands(settings: dict[str, object]) -> list[str]:
    hooks = settings["hooks"]
    assert isinstance(hooks, dict)
    entries = hooks["SessionStart"]
    assert isinstance(entries, list)
    out: list[str] = []
    for entry in entries:
        for hook in entry.get("hooks", []):  # type: ignore[union-attr]
            out.append(hook["command"])
    return out

_CANONICAL_CAPABILITY_KEYS = {
    "pre_tool_use_hook",
    "post_tool_use_hook",
    "session_start_hook",
    "session_end_hook",
    "pre_commit_hook",
    "rules_file_injection",
    "mcp_server",
    "subprocess_execution",
    "turn_end_feedback_hook",
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
        "turn_end_feedback_hook": True,
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
        _which_both,
    )
    ClaudeCodeAdapter().install_hooks(tmp_path)

    settings_path = tmp_path / ".claude" / "settings.local.json"
    assert settings_path.exists()
    settings = json.loads(settings_path.read_text())
    cmds = _commands(settings)
    assert cmds == [_EXPECTED_COMMAND]
    # SessionStart IS wired (Task 8): `<abs super-harness> change resume`.
    assert _session_commands(settings) == [_EXPECTED_SESSION_COMMAND]
    # SessionStart entry carries no tool matcher (fires on all session sources).
    assert "matcher" not in settings["hooks"]["SessionStart"][0]
    # The PreToolUse matcher comes from the merge util's canonical set.
    assert settings["hooks"]["PreToolUse"][0]["matcher"] == _MATCHER
    # No `.sh` script is written anywhere.
    assert glob.glob(str(tmp_path / "**" / "*.sh"), recursive=True) == []


def test_install_hooks_missing_hook_binary_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "super_harness.adapters.agent.claude_code.shutil.which",
        lambda _name: None,
    )
    with pytest.raises(RuntimeError, match="super-harness-hook"):
        ClaudeCodeAdapter().install_hooks(tmp_path)
    # Nothing written before the abort.
    assert not (tmp_path / ".claude" / "settings.local.json").exists()


def test_install_hooks_missing_cli_binary_raises_before_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`super-harness-hook` resolves but `super-harness` (CLI) does not → abort
    BEFORE writing either hook (both binaries resolved up front)."""
    monkeypatch.setattr(
        "super_harness.adapters.agent.claude_code.shutil.which",
        lambda name: _RESOLVED if name == "super-harness-hook" else None,
    )
    with pytest.raises(RuntimeError, match="super-harness"):
        ClaudeCodeAdapter().install_hooks(tmp_path)
    assert not (tmp_path / ".claude" / "settings.local.json").exists()


def test_install_hooks_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "super_harness.adapters.agent.claude_code.shutil.which",
        _which_both,
    )
    adapter = ClaudeCodeAdapter()
    adapter.install_hooks(tmp_path)
    adapter.install_hooks(tmp_path)

    settings = json.loads((tmp_path / ".claude" / "settings.local.json").read_text())
    assert _commands(settings).count(_EXPECTED_COMMAND) == 1
    assert _session_commands(settings).count(_EXPECTED_SESSION_COMMAND) == 1


def test_install_hooks_rolls_back_on_second_merge_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the SECOND merge raises mid-install, settings.local.json is restored to
    its exact pre-install state (snapshot rollback), not left half-written.

    Both binaries resolve (so we get past the up-front check). We force the
    SessionStart merge to raise; the PreToolUse merge has already mutated the
    file by then. Rollback must rewrite the original pre-install bytes.
    """
    settings_path = tmp_path / ".claude" / "settings.local.json"
    settings_path.parent.mkdir(parents=True)
    pristine = json.dumps({"model": "claude-opus", "hooks": {}}, indent=2) + "\n"
    settings_path.write_text(pristine)

    monkeypatch.setattr(
        "super_harness.adapters.agent.claude_code.shutil.which",
        _which_both,
    )

    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("simulated SessionStart merge failure")

    monkeypatch.setattr(
        "super_harness.adapters.agent.claude_code.merge_session_start_hook",
        boom,
    )

    with pytest.raises(RuntimeError, match="simulated SessionStart"):
        ClaudeCodeAdapter().install_hooks(tmp_path)

    # Snapshot restored: byte-identical to the pre-install file.
    assert settings_path.read_text() == pristine


def test_install_hooks_rolls_back_to_absent_when_file_was_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If settings.local.json did not exist pre-install and a merge fails, rollback
    DELETES the file (restores the 'absent' snapshot)."""
    settings_path = tmp_path / ".claude" / "settings.local.json"
    assert not settings_path.exists()

    monkeypatch.setattr(
        "super_harness.adapters.agent.claude_code.shutil.which",
        _which_both,
    )

    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("simulated SessionStart merge failure")

    monkeypatch.setattr(
        "super_harness.adapters.agent.claude_code.merge_session_start_hook",
        boom,
    )

    with pytest.raises(RuntimeError, match="simulated SessionStart"):
        ClaudeCodeAdapter().install_hooks(tmp_path)

    assert not settings_path.exists()


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


def test_agents_md_subsection_teaches_compiled_review_contract() -> None:
    block = " ".join(ClaudeCodeAdapter().agents_md_subsection().split()).lower()
    assert "dispatch every assignment in listed order" in block
    assert "apply its agent_options verbatim" in block
    assert "collect every raw verdict before recording" in block
    assert "does not trigger plan review" in block
    assert "plan, scope, or requirements changed" in block
    assert "never widen an assignment to the whole pr" in block


def test_agents_md_subsection_has_review_protocol() -> None:
    # HG-02.B: teach the agent the review loop — dispatch a reviewer subagent and
    # record the verdict via the CLI (the harness never runs the review itself).
    block = ClaudeCodeAdapter().agents_md_subsection()
    assert "AWAITING_PLAN_REVIEW" in block
    assert "AWAITING_CODE_REVIEW" in block
    assert "super-harness review approve" in block
    assert "super-harness review reject" in block
    # Tells the agent to dispatch a reviewer subagent (uses its own Task tool).
    assert "subagent" in block.lower()
    # HG-02.C: strategy-aware — the agent checks the configured strategy and, when
    # it is `human`, hands off instead of self-approving.
    assert "strategy" in block.lower()
    assert "human" in block.lower()
    assert "agent_options" in block
    assert "bundle-only" in block
    assert "incremental" in block


def test_agents_md_subsection_frames_kill_switch_as_human_only() -> None:
    # The agent-facing subsection must NOT teach the kill switch as an escape
    # hatch. It is a human-only emergency override; the agent must stop and
    # surface the block to the human instead of bypassing the gate.
    block = ClaudeCodeAdapter().agents_md_subsection()
    assert "gate-disabled" not in block
    assert "Escape hatch" not in block
    assert "human-only" in block
    assert "surface" in block.lower()


def test_on_uninstall_restores_earliest_pristine_backup(tmp_path: Path) -> None:
    """install_hooks runs THREE merges → THREE backups on a pre-existing file.

    The EARLIEST (lowest ts) backup is the truly pristine file; the later ones
    already contains our PreToolUse entry. Uninstall must restore the earliest
    so BOTH of our hooks are removed — restoring the newest would leave the
    PreToolUse hook behind.
    """
    settings_path = tmp_path / ".claude" / "settings.local.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(json.dumps({"hooks": {"PreToolUse": ["mutated"]}}))
    pristine = {"model": "claude-opus", "hooks": {}}
    # ts=100: pristine (1st merge's backup). ts=200: pristine + our PreToolUse
    # (2nd merge's backup). The earliest (100) must win.
    settings_path.with_name(
        "settings.local.json.super-harness-backup.100"
    ).write_text(json.dumps(pristine))
    settings_path.with_name(
        "settings.local.json.super-harness-backup.200"
    ).write_text(
        json.dumps(
            {
                "model": "claude-opus",
                "hooks": {"PreToolUse": ["ours"]},
            }
        )
    )

    ClaudeCodeAdapter().on_uninstall(tmp_path)

    assert json.loads(settings_path.read_text()) == pristine


def test_install_then_uninstall_round_trip_restores_pristine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: install BOTH hooks on a pre-existing file, then uninstall —
    the file is restored to its exact pristine pre-install content."""
    monkeypatch.setattr(
        "super_harness.adapters.agent.claude_code.shutil.which",
        _which_both,
    )
    settings_path = tmp_path / ".claude" / "settings.local.json"
    settings_path.parent.mkdir(parents=True)
    pristine = {"model": "claude-opus", "permissions": {"allow": ["Bash(ls:*)"]}}
    settings_path.write_text(json.dumps(pristine, indent=2))

    adapter = ClaudeCodeAdapter()
    adapter.install_hooks(tmp_path)
    # Sanity: both hooks present after install.
    settings = json.loads(settings_path.read_text())
    assert _commands(settings) == [_EXPECTED_COMMAND]
    assert _session_commands(settings) == [_EXPECTED_SESSION_COMMAND]

    adapter.on_uninstall(tmp_path)

    assert json.loads(settings_path.read_text()) == pristine


def test_on_uninstall_no_backup_is_noop(tmp_path: Path) -> None:
    settings_path = tmp_path / ".claude" / "settings.local.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(json.dumps({"model": "keep"}))

    # No backup files present — best-effort uninstall must leave the file as-is.
    ClaudeCodeAdapter().on_uninstall(tmp_path)

    assert json.loads(settings_path.read_text()) == {"model": "keep"}


def test_claude_adapter_install_detail_strings():
    from super_harness.adapters.agent.claude_code import ClaudeCodeAdapter

    a = ClaudeCodeAdapter()
    assert a.local_config_relpath() == ".claude/settings.local.json"
    detail = a.installed_detail()
    assert "PreToolUse" in detail and "Stop" in detail
    assert ".claude/settings.local.json" in detail


def test_agents_md_subsection_does_not_teach_kill_switch():
    sub = ClaudeCodeAdapter().agents_md_subsection()
    assert "gate-disabled" not in sub
    assert "surface" in sub.lower()
    assert "human" in sub.lower()
    # Generalized wording: keep the "don't work around the gate, surface to human"
    # norm without naming/pointing at the override file or a discrete "kill switch".
    assert "override file" not in sub.lower()
    assert "kill switch" not in sub.lower()
    flat = " ".join(sub.split()).lower()  # collapse line-wraps before phrase match
    assert "work around the gate" in flat


# --- authoring-time Stop feedback (2026-07-01) ---

def _stop_verdict():
    from super_harness.core.authoring_check import Verdict, Violation
    return Verdict(violations=[Violation(
        "d-core-is-base",
        "core is not allowed to import super_harness.sensors",
        "docs/decisions/d-core-is-base.md")])


def test_claude_format_stop_feedback_blocks_with_reason():
    import json

    from super_harness.adapters.agent.claude_code import ClaudeCodeAdapter
    out = ClaudeCodeAdapter().format_stop_feedback(_stop_verdict())
    obj = json.loads(out)
    assert obj["decision"] == "block"
    assert "d-core-is-base" in obj["reason"]
    assert "super_harness.sensors" in obj["reason"]
    assert "docs/decisions/d-core-is-base.md" in obj["reason"]


def test_claude_format_stop_feedback_clean_is_empty():
    from super_harness.adapters.agent.claude_code import ClaudeCodeAdapter
    from super_harness.core.authoring_check import Verdict
    assert ClaudeCodeAdapter().format_stop_feedback(Verdict(violations=[])) == ""


def test_claude_stop_should_check_skips_continuation():
    a = ClaudeCodeAdapter()
    assert a.stop_should_check({"stop_hook_active": True}) is False  # continuation → skip
    assert a.stop_should_check({"stop_hook_active": False}) is True
    assert a.stop_should_check({}) is True


def _install_into(tmp_path, monkeypatch, pre_existing):
    import json

    import super_harness.adapters.agent.claude_code as cc
    from super_harness.adapters.agent.claude_code import ClaudeCodeAdapter
    monkeypatch.setattr(cc.shutil, "which", lambda n: f"/abs/{n}")
    (tmp_path / ".claude").mkdir()
    f = tmp_path / ".claude" / "settings.local.json"
    if pre_existing is not None:
        f.write_text(json.dumps(pre_existing))
    ClaudeCodeAdapter().install_hooks(tmp_path)
    return f


def test_install_registers_stop(tmp_path, monkeypatch):
    import json
    f = _install_into(tmp_path, monkeypatch, pre_existing=None)
    events = json.loads(f.read_text())["hooks"]
    assert "Stop" in events and "PreToolUse" in events
    assert any("--event stop" in h["command"] for e in events["Stop"] for h in e["hooks"])


def test_uninstall_round_trip_removes_stop(tmp_path, monkeypatch):
    import json

    from super_harness.adapters.agent.claude_code import ClaudeCodeAdapter
    pristine = {"model": "x", "permissions": {}}
    f = _install_into(tmp_path, monkeypatch, pre_existing=pristine)
    assert "Stop" in json.loads(f.read_text())["hooks"]
    ClaudeCodeAdapter().on_uninstall(tmp_path)
    assert json.loads(f.read_text()) == pristine


def test_stop_advisory_has_no_self_authorized_bypass():
    # Regression lock (#51/#52): the advisory must NOT teach the agent a
    # self-authorized bypass; it directs legitimate exceptions to the human.
    import json

    from super_harness.adapters.agent.claude_code import ClaudeCodeAdapter
    reason = json.loads(ClaudeCodeAdapter().format_stop_feedback(_stop_verdict()))["reason"]
    low = reason.lower()
    assert "deliberate, disclosed exception, proceed" not in low
    assert "proceed on your own authority" in low  # explicitly forbidden
    assert "surface it to the human" in low
