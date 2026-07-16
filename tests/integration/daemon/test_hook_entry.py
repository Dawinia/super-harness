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


def test_block_records_a_gate_block_line(tmp_path: Path) -> None:
    """Stage 2: a real BLOCK writes one telemetry record with the tool + file +
    lifecycle state that triggered it."""
    from super_harness.core.gate_blocks import read_blocks
    from super_harness.core.paths import gate_blocks_path

    _init(tmp_path, "c1", "INTENT_DECLARED")
    payload = json.dumps({"tool_name": "Write", "tool_input": {"file_path": "src/x.py"}})
    res = _run(tmp_path, "--agent", "claude-code", stdin=payload)
    assert res.returncode == 2  # blocked
    recs = read_blocks(gate_blocks_path(tmp_path))
    assert len(recs) == 1
    r = recs[0]
    assert (r.change_id, r.state, r.tool, r.file) == (
        "c1", "INTENT_DECLARED", "Write", "src/x.py",
    )


def test_allow_records_nothing(tmp_path: Path) -> None:
    """An ALLOW (edit-permitted state) writes no telemetry — only BLOCKs count."""
    from super_harness.core.paths import gate_blocks_path

    _init(tmp_path, "c1", "IMPLEMENTATION_IN_PROGRESS")
    assert _run(tmp_path, "Edit", "f.py").returncode == 0
    assert not gate_blocks_path(tmp_path).exists()


def test_block_still_blocks_when_recording_fails(tmp_path: Path, monkeypatch) -> None:
    """HEADLINE SAFETY PROPERTY: a failing telemetry write must NOT flip a real
    BLOCK into a fail-open ALLOW (an uncaught hook exception is exit 1 =
    non-blocking for the Claude shim). Recording raises → verdict stays 'block',
    no exception escapes `_decide`."""
    from super_harness.daemon import hook_entry

    _init(tmp_path, "c1", "INTENT_DECLARED")
    monkeypatch.chdir(tmp_path)

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr("super_harness.core.gate_blocks.record_block", boom)
    decision, _reason, _suggested = hook_entry._decide("Write", "src/x.py")
    assert decision == "block"
