"""Unit tests for the in-process PreToolUse decision core (design 2026-07-03).

No daemon, no socket, no timeout knob: `_decide` resolves the workspace, honours
the kill switch, loads ONE state snapshot, and runs the pure PreToolUseGate. The
block message now carries the state's `suggested_action` (F8).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from super_harness.daemon import hook_entry


def _init_state(root: Path, change_id: str, state: str, at: str = "2026-07-02T00:00:00Z") -> None:
    harness = root / ".harness"
    harness.mkdir(parents=True, exist_ok=True)
    (harness / "state.yaml").write_text(
        "changes:\n"
        f"  {change_id}:\n    change_id: {change_id}\n"
        f"    current_state: {state}\n    last_event_at: '{at}'\n",
        encoding="utf-8",
    )


@pytest.fixture()
def in_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SUPER_HARNESS_CHANGE_ID", raising=False)
    return tmp_path


def test_no_harness_allows(in_workspace: Path) -> None:
    decision, _reason, suggested = hook_entry._decide("Edit", "f.py")
    assert decision == "allow"
    assert suggested is None


def test_blocking_state_returns_suggestion(in_workspace: Path) -> None:
    _init_state(in_workspace, "c1", "INTENT_DECLARED")
    decision, reason, suggested = hook_entry._decide("Edit", "f.py")
    assert decision == "block"
    assert "INTENT_DECLARED" in reason
    assert suggested == "Draft a plan, then mark it ready, then retry the edit."


def test_allowing_state_allows(in_workspace: Path) -> None:
    _init_state(in_workspace, "c1", "IMPLEMENTATION_IN_PROGRESS")
    decision, _reason, _suggested = hook_entry._decide("Edit", "f.py")
    assert decision == "allow"


def test_env_override_selects_change(in_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = in_workspace / ".harness"
    harness.mkdir(parents=True, exist_ok=True)
    (harness / "state.yaml").write_text(
        "changes:\n"
        "  live:\n    change_id: live\n    current_state: PLAN_APPROVED\n"
        "    last_event_at: '2026-07-02T00:00:00Z'\n"
        "  frozen:\n    change_id: frozen\n    current_state: READY_TO_MERGE\n"
        "    last_event_at: '2026-07-01T00:00:00Z'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SUPER_HARNESS_CHANGE_ID", "frozen")
    decision, _reason, suggested = hook_entry._decide("Edit", "f.py")
    assert decision == "block"
    assert suggested == "Open/merge the PR; do not edit further."


def test_kill_switch_allows_and_records_bypass(
    in_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_state(in_workspace, "c1", "INTENT_DECLARED")  # would block
    (in_workspace / ".harness" / "gate-disabled").touch()
    recorded: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        hook_entry, "_record_bypass",
        lambda root, *, tool, file: recorded.append((tool, file)),
    )
    decision, _reason, _suggested = hook_entry._decide("Edit", "f.py")
    assert decision == "allow"
    assert recorded == [("Edit", "f.py")]


def test_corrupt_state_fails_open(in_workspace: Path) -> None:
    harness = in_workspace / ".harness"
    harness.mkdir(parents=True, exist_ok=True)
    (harness / "state.yaml").write_text("changes: {oops\n", encoding="utf-8")
    decision, _reason, _suggested = hook_entry._decide("Edit", "f.py")
    assert decision == "allow"


def test_format_block_includes_suggestion_and_halt_hint() -> None:
    msg = hook_entry._format_block(
        "READY_TO_MERGE: ready for merge", "Open/merge the PR; do not edit further."
    )
    assert "BLOCK (READY_TO_MERGE: ready for merge)" in msg
    assert "Open/merge the PR" in msg
    assert "Stop and tell the human" in msg


def test_format_block_without_suggestion() -> None:
    msg = hook_entry._format_block("unknown state: WAT", None)
    assert "BLOCK (unknown state: WAT)" in msg
    assert "Stop and tell the human" in msg
