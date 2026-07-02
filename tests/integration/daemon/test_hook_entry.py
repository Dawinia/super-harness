"""Integration tests for the `super-harness-hook` binary — in-process decision
(design 2026-07-03). Drives the installed console-script as a real subprocess;
asserts exit codes and the F8 suggestion in the block message. No daemon.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path


def _init(root: Path, change_id: str | None = None, state: str | None = None) -> None:
    harness = root / ".harness"
    harness.mkdir(parents=True, exist_ok=True)
    if change_id and state:
        (harness / "state.yaml").write_text(
            "changes:\n"
            f"  {change_id}:\n    change_id: {change_id}\n"
            f"    current_state: {state}\n    last_event_at: '2026-07-02T00:00:00Z'\n",
            encoding="utf-8",
        )


def _run(root: Path, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["super-harness-hook", *args], cwd=str(root), input=stdin,
        capture_output=True, text=True,
    )


def test_positional_block_carries_suggestion(tmp_path: Path) -> None:
    _init(tmp_path, "c1", "INTENT_DECLARED")
    res = _run(tmp_path, "Edit", "f.py")
    assert res.returncode == 1
    assert "BLOCK (INTENT_DECLARED" in res.stderr
    assert "Draft a plan" in res.stderr


def test_positional_allow(tmp_path: Path) -> None:
    _init(tmp_path, "c1", "IMPLEMENTATION_IN_PROGRESS")
    assert _run(tmp_path, "Edit", "f.py").returncode == 0


def test_no_harness_allows(tmp_path: Path) -> None:
    assert _run(tmp_path, "Edit", "f.py").returncode == 0


def test_claude_code_shim_blocks_exit_2(tmp_path: Path) -> None:
    _init(tmp_path, "c1", "READY_TO_MERGE")
    payload = json.dumps({"tool_name": "Edit", "tool_input": {"file_path": "f.py"}})
    res = _run(tmp_path, "--agent", "claude-code", stdin=payload)
    assert res.returncode == 2
    assert "Open/merge the PR" in res.stderr


def test_codex_shim_deny_json(tmp_path: Path) -> None:
    _init(tmp_path, "c1", "AWAITING_CODE_REVIEW")
    payload = json.dumps({"tool_name": "Shell", "tool_input": {"command": "echo hi"}})
    res = _run(tmp_path, "--agent", "codex", stdin=payload)
    assert res.returncode == 0
    out = json.loads(res.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "review" in out["hookSpecificOutput"]["permissionDecisionReason"].lower()


def test_kill_switch_allows(tmp_path: Path) -> None:
    _init(tmp_path, "c1", "INTENT_DECLARED")
    (tmp_path / ".harness" / "gate-disabled").touch()
    assert _run(tmp_path, "Edit", "f.py").returncode == 0
