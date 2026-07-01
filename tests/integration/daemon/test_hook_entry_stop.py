"""Integration tests for the Stop-hook authoring-time path of `super-harness-hook`.

Invokes the real console script (like `test_hook_entry.py`), feeding a Claude Code
Stop payload on stdin. The path is non-blocking (always exit 0), loop-safe
(`stop_hook_active`), fail-open (no harness / kill switch / error → silent allow).
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path


def _run_stop(cwd: Path, payload: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["super-harness-hook", "--agent", "claude-code", "--event", "stop"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=str(cwd),
    )


def _workspace_with_failing_opted_check(tmp_path: Path) -> None:
    (tmp_path / ".harness").mkdir()
    d = tmp_path / "docs" / "decisions"
    d.mkdir(parents=True)
    (d / "d-fail.md").write_text(
        "---\nid: d-fail\nstatus: ratified\nauthoring_time: true\n---\n"
        "body\n```check\nfalse\n```\n"
        "```counterexample path=src/_ce.py\nx = 1\n```\n"
    )


def test_stop_violation_blocks_with_reason(tmp_path: Path):
    _workspace_with_failing_opted_check(tmp_path)
    r = _run_stop(tmp_path, {"hook_event_name": "Stop", "stop_hook_active": False})
    assert r.returncode == 0
    obj = json.loads(r.stdout)
    assert obj["decision"] == "block"
    assert "d-fail" in obj["reason"]


def test_stop_already_nudged_allows(tmp_path: Path):
    _workspace_with_failing_opted_check(tmp_path)
    r = _run_stop(tmp_path, {"hook_event_name": "Stop", "stop_hook_active": True})
    assert r.returncode == 0
    assert r.stdout.strip() == ""  # loop-safe: never block twice


def test_stop_no_harness_is_silent(tmp_path: Path):
    r = _run_stop(tmp_path, {"hook_event_name": "Stop", "stop_hook_active": False})
    assert r.returncode == 0
    assert r.stdout.strip() == ""


def test_stop_kill_switch_allows(tmp_path: Path):
    _workspace_with_failing_opted_check(tmp_path)
    (tmp_path / ".harness" / "gate-disabled").touch()
    r = _run_stop(tmp_path, {"hook_event_name": "Stop", "stop_hook_active": False})
    assert r.returncode == 0
    assert r.stdout.strip() == ""


def test_stop_clean_workspace_allows(tmp_path: Path):
    # opted-in decision whose check PASSES → clean → allow the stop silently
    (tmp_path / ".harness").mkdir()
    d = tmp_path / "docs" / "decisions"
    d.mkdir(parents=True)
    (d / "d-ok.md").write_text(
        "---\nid: d-ok\nstatus: ratified\nauthoring_time: true\n---\n"
        "body\n```check\ntrue\n```\n"
        "```counterexample path=src/_ce.py\nx = 1\n```\n"
    )
    r = _run_stop(tmp_path, {"hook_event_name": "Stop", "stop_hook_active": False})
    assert r.returncode == 0
    assert r.stdout.strip() == ""


def test_stop_malformed_stdin_is_silent(tmp_path: Path):
    _workspace_with_failing_opted_check(tmp_path)
    r = subprocess.run(
        ["super-harness-hook", "--agent", "claude-code", "--event", "stop"],
        input="not json at all", capture_output=True, text=True, cwd=str(tmp_path),
    )
    assert r.returncode == 0  # fail-open on malformed stdin
    assert r.stdout.strip() == ""


# --- Codex rides the SAME agnostic _run_stop orchestrator --------------------
def _run_codex_stop(cwd: Path, payload: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["super-harness-hook", "--agent", "codex", "--event", "stop"],
        input=json.dumps(payload), capture_output=True, text=True, cwd=str(cwd),
    )


def test_codex_stop_violation_blocks_with_reason(tmp_path: Path):
    _workspace_with_failing_opted_check(tmp_path)
    r = _run_codex_stop(tmp_path, {"hook_event_name": "Stop", "stop_hook_active": False})
    assert r.returncode == 0
    obj = json.loads(r.stdout)
    assert obj["decision"] == "block" and "d-fail" in obj["reason"]
    assert set(obj) == {"decision", "reason"}  # reason-ONLY


def test_codex_stop_already_nudged_allows(tmp_path: Path):
    _workspace_with_failing_opted_check(tmp_path)
    r = _run_codex_stop(tmp_path, {"hook_event_name": "Stop", "stop_hook_active": True})
    assert r.returncode == 0 and r.stdout.strip() == ""


def test_codex_stop_kill_switch_allows(tmp_path: Path):
    _workspace_with_failing_opted_check(tmp_path)
    (tmp_path / ".harness" / "gate-disabled").touch()
    r = _run_codex_stop(tmp_path, {"hook_event_name": "Stop", "stop_hook_active": False})
    assert r.returncode == 0 and r.stdout.strip() == ""
