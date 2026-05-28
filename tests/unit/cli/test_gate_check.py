"""Unit tests for `super-harness gate check` (Phase 5 Task 5.2).

`gate check` is the manual/CI/debug entry to the pre-tool-use gate decision
*through the daemon* (NOT the hot path — that's `super-harness-hook`). These
tests monkeypatch `supervisor.gate_pre_tool_use` so no real daemon is needed,
mirroring the CliRunner conventions in `test_gate.py`.

Coverage:
  1. pre-tool-use ALLOW  → exit 0 (EXIT_OK)
  2. pre-tool-use BLOCK  → exit 2 (EXIT_VALIDATION)
  3. cold-path gate name → exit 1 (EXIT_GENERIC, "not yet implemented"),
     NOT a click usage error (the 5-name Choice is RATIFIED).
"""
from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from super_harness.cli import main


def test_gate_check_allow(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / ".harness").mkdir()
    monkeypatch.setattr(
        "super_harness.cli.gate.supervisor.gate_pre_tool_use",
        lambda root, *, tool, file, change_id: ("allow", "PLAN_APPROVED: ok"),
    )
    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "gate", "check", "pre-tool-use",
         "--tool", "Edit", "--file", "a.py"],
    )
    assert r.exit_code == 0


def test_gate_check_block_exit_2(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / ".harness").mkdir()
    monkeypatch.setattr(
        "super_harness.cli.gate.supervisor.gate_pre_tool_use",
        lambda root, *, tool, file, change_id: (
            "block",
            "INTENT_DECLARED: plan not drafted yet",
        ),
    )
    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "gate", "check", "pre-tool-use",
         "--tool", "Edit", "--file", "a.py"],
    )
    assert r.exit_code == 2  # EXIT_VALIDATION


def test_gate_check_cold_path_not_implemented(tmp_path: Path) -> None:
    (tmp_path / ".harness").mkdir()
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "gate", "check", "pre-commit"]
    )
    assert r.exit_code == 1  # EXIT_GENERIC, clear "not yet implemented" message
    combined = r.output + (r.stderr or "")
    assert "not yet implemented" in combined
