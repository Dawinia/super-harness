"""End-to-end tests for the `super-harness-hook` binary per
daemon-architecture §3.5 + cli-command-surface §3.1.

These tests invoke the actual entry-point via `subprocess.run` (the
real PreToolUse path), not the Python function — that's the only way
to verify the click-less import chain stays click-less + the entry-point
is registered in pyproject.toml. Tests skip if the binary isn't on PATH.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from tests.integration.daemon.conftest import kill_daemon, write_state


def _has_hook_binary() -> bool:
    return shutil.which("super-harness-hook") is not None


pytestmark = pytest.mark.skipif(
    not _has_hook_binary(),
    reason="super-harness-hook not installed; run `pip install -e .`",
)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / ".harness").mkdir()
    return tmp_path


def _start_daemon(workspace: Path) -> None:
    """Use the supervisor to ensure daemon is up (foreground / blocking).

    `wait_seconds=15.0` (vs prod default 5.0) is defensive against slow CI
    daemon-spawn races. The autouse fixture in conftest also widens the hook
    subprocess's hot-path query timeout via SUPER_HARNESS_HOOK_QUERY_TIMEOUT
    so a cold subprocess + daemon-query under load cannot fail-open silently.
    Both knobs root-cause OPEN-ITEMS #4 (daemon readiness determinism).
    """
    from super_harness.daemon import supervisor
    supervisor.ensure_running(workspace, wait_seconds=15.0)


def test_hook_entry_exits_0_on_allow(workspace: Path) -> None:
    write_state(workspace, "c1", "PLAN_APPROVED")
    _start_daemon(workspace)
    try:
        env = {**os.environ, "SUPER_HARNESS_CHANGE_ID": "c1"}
        result = subprocess.run(
            ["super-harness-hook", "Edit", "src/foo.py"],
            cwd=workspace,
            capture_output=True,
            env=env,
            timeout=5.0,
        )
        assert result.returncode == 0, result.stderr.decode()
    finally:
        kill_daemon(workspace)


def test_hook_entry_exits_1_on_block(workspace: Path) -> None:
    write_state(workspace, "c1", "AWAITING_PLAN_REVIEW")
    _start_daemon(workspace)
    try:
        env = {**os.environ, "SUPER_HARNESS_CHANGE_ID": "c1"}
        result = subprocess.run(
            ["super-harness-hook", "Edit", "src/foo.py"],
            cwd=workspace,
            capture_output=True,
            env=env,
            timeout=5.0,
        )
        assert result.returncode == 1
        assert b"AWAITING_PLAN_REVIEW" in result.stderr
    finally:
        kill_daemon(workspace)


def test_hook_entry_exits_0_when_no_harness(tmp_path: Path) -> None:
    """No .harness/ on walk-up → exit 0 (Axiom 1: prevent, not punish)."""
    # `tmp_path` here is a workspace with NO .harness dir.
    result = subprocess.run(
        ["super-harness-hook", "Edit", "src/foo.py"],
        cwd=tmp_path,
        capture_output=True,
        timeout=5.0,
    )
    assert result.returncode == 0


def test_hook_entry_exits_0_on_daemon_down_fail_safe(workspace: Path) -> None:
    """AC-2: daemon down → fail-safe ALLOW (exit 0) + stderr warn."""
    write_state(workspace, "c1", "PLAN_APPROVED")
    # Deliberately do NOT start the daemon.
    try:
        env = {**os.environ, "SUPER_HARNESS_CHANGE_ID": "c1"}
        result = subprocess.run(
            ["super-harness-hook", "Edit", "src/foo.py"],
            cwd=workspace,
            capture_output=True,
            env=env,
            timeout=5.0,
        )
        assert result.returncode == 0
        # Optional: stderr mentions fail-safe / daemon. Not strictly enforced
        # to avoid coupling test to copy.
    finally:
        kill_daemon(workspace)


def test_hook_entry_derives_active_change_from_state_yaml(workspace: Path) -> None:
    """When env var unset, hook should derive the active change_id from
    state.yaml's `changes` map (first non-terminal change), NOT a top-level
    `active_change_id` field (which the reducer never writes)."""
    write_state(workspace, "c1", "PLAN_APPROVED")
    _start_daemon(workspace)
    try:
        env = {k: v for k, v in os.environ.items()
               if k != "SUPER_HARNESS_CHANGE_ID"}
        result = subprocess.run(
            ["super-harness-hook", "Edit", "src/foo.py"],
            cwd=workspace,
            capture_output=True,
            env=env,
            timeout=5.0,
        )
        assert result.returncode == 0, result.stderr.decode()
    finally:
        kill_daemon(workspace)


def test_hook_entry_resolves_active_change_without_env(workspace: Path) -> None:
    """No SUPER_HARNESS_CHANGE_ID → hook derives the active (first non-terminal)
    change from state.yaml's `changes` map (reducer shape has NO active_change_id
    field). A blocking state must actually block.

    Regression guard: the hook previously read state.yaml::active_change_id, a
    field the reducer never writes — so without the env var it resolved None and
    the gate never auto-blocked (smoke caught it). This drives the real reducer
    shape with no env var and asserts the block actually fires.
    """
    # Write REAL reducer shape: changes map only, NO active_change_id.
    (workspace / ".harness" / "state.yaml").write_text(
        yaml.safe_dump(
            {"changes": {"c1": {"change_id": "c1",
                                "current_state": "AWAITING_PLAN_REVIEW"}}}
        )
    )
    _start_daemon(workspace)
    try:
        env = {k: v for k, v in os.environ.items()
               if k != "SUPER_HARNESS_CHANGE_ID"}
        result = subprocess.run(
            ["super-harness-hook", "Edit", "src/foo.py"],
            cwd=workspace,
            capture_output=True,
            env=env,
            timeout=5.0,
        )
        assert result.returncode == 1, result.stdout + result.stderr  # BLOCK
        assert b"AWAITING_PLAN_REVIEW" in result.stderr
    finally:
        kill_daemon(workspace)


def test_hook_entry_exits_0_on_empty_argv() -> None:
    """No tool argument -> exit 0 (permissive; hook must not block on a call
    shape it doesn't understand). No .harness / daemon needed — main() exits
    before find_harness_root when argv is empty."""
    result = subprocess.run(
        ["super-harness-hook"], capture_output=True, timeout=5.0,
    )
    assert result.returncode == 0, result.stderr.decode()


def test_hook_entry_handles_tool_with_no_file_arg(workspace: Path) -> None:
    """A tool with no file argument (e.g. Bash) -> file=None flows through to
    the gate without crashing. PLAN_APPROVED -> allow (exit 0)."""
    write_state(workspace, "c1", "PLAN_APPROVED")
    _start_daemon(workspace)
    try:
        env = {**os.environ, "SUPER_HARNESS_CHANGE_ID": "c1"}
        result = subprocess.run(
            ["super-harness-hook", "Bash"],  # tool only, NO file arg
            cwd=workspace, capture_output=True, env=env, timeout=5.0,
        )
        assert result.returncode == 0, result.stderr.decode()
    finally:
        kill_daemon(workspace)


# ---------------------------------------------------------------------------
# `--agent claude-code` shim mode (Task 5.3)
#
# Claude Code PreToolUse hooks deliver input as JSON on STDIN (not argv) and
# treat exit 2 = block (stderr → model), exit 1 = NON-blocking error (tool
# proceeds!). So the shim must read stdin JSON and exit 2 on block — never 1.
# The decision core is shared with positional mode; only input parsing + the
# block exit-code (2 vs 1) differ.
# ---------------------------------------------------------------------------


def test_claude_code_shim_exits_2_on_block(workspace: Path) -> None:
    """Claude Code shim: blocking state → exit 2 (NOT 1), reason on stderr."""
    write_state(workspace, "c1", "AWAITING_PLAN_REVIEW")
    _start_daemon(workspace)
    try:
        env = {**os.environ, "SUPER_HARNESS_CHANGE_ID": "c1"}
        stdin = '{"tool_name":"Edit","tool_input":{"file_path":"a.py"}}'
        result = subprocess.run(
            ["super-harness-hook", "--agent", "claude-code"],
            cwd=workspace,
            capture_output=True,
            env=env,
            input=stdin.encode(),
            timeout=5.0,
        )
        assert result.returncode == 2, result.stderr.decode()
        assert b"AWAITING_PLAN_REVIEW" in result.stderr
    finally:
        kill_daemon(workspace)


def test_codex_shim_denies_via_stdout_json_on_block(workspace: Path) -> None:
    """Codex shim: blocking state → exit 0 + deny JSON on STDOUT (not stderr/exit-2)."""
    write_state(workspace, "c1", "AWAITING_PLAN_REVIEW")
    _start_daemon(workspace)
    try:
        env = {**os.environ, "SUPER_HARNESS_CHANGE_ID": "c1"}
        stdin = '{"tool_name":"apply_patch","tool_input":{"command":"*** patch"}}'
        result = subprocess.run(
            ["super-harness-hook", "--agent", "codex"],
            cwd=workspace, capture_output=True, env=env,
            input=stdin.encode(), timeout=5.0,
        )
        assert result.returncode == 0, result.stderr.decode()
        out = json.loads(result.stdout.decode())
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "AWAITING_PLAN_REVIEW" in out["hookSpecificOutput"]["permissionDecisionReason"]
    finally:
        kill_daemon(workspace)


def test_claude_code_shim_exits_0_on_allow(workspace: Path) -> None:
    """Claude Code shim: allowing state → exit 0."""
    write_state(workspace, "c1", "PLAN_APPROVED")
    _start_daemon(workspace)
    try:
        env = {**os.environ, "SUPER_HARNESS_CHANGE_ID": "c1"}
        stdin = '{"tool_name":"Edit","tool_input":{"file_path":"a.py"}}'
        result = subprocess.run(
            ["super-harness-hook", "--agent", "claude-code"],
            cwd=workspace,
            capture_output=True,
            env=env,
            input=stdin.encode(),
            timeout=5.0,
        )
        assert result.returncode == 0, result.stderr.decode()
    finally:
        kill_daemon(workspace)


def test_claude_code_shim_fail_open_on_daemon_down(workspace: Path) -> None:
    """Claude Code shim: daemon down → fail-open ALLOW (exit 0), never block.

    A would-be blocking state must still surface as exit 0 here because the
    supervisor fail-open ALLOWs when the daemon is unreachable. Exit 2 on a
    daemon-down path would be a fail-closed regression."""
    write_state(workspace, "c1", "AWAITING_PLAN_REVIEW")
    # Deliberately do NOT start the daemon.
    try:
        env = {**os.environ, "SUPER_HARNESS_CHANGE_ID": "c1"}
        stdin = '{"tool_name":"Edit","tool_input":{"file_path":"a.py"}}'
        result = subprocess.run(
            ["super-harness-hook", "--agent", "claude-code"],
            cwd=workspace,
            capture_output=True,
            env=env,
            input=stdin.encode(),
            timeout=5.0,
        )
        assert result.returncode == 0, result.stderr.decode()
    finally:
        kill_daemon(workspace)


def test_claude_code_shim_fail_open_on_malformed_stdin(workspace: Path) -> None:
    """Claude Code shim: malformed stdin JSON → fail-open ALLOW (exit 0).

    No daemon needed — main() bails out before touching the workspace when the
    stdin payload can't be parsed."""
    result = subprocess.run(
        ["super-harness-hook", "--agent", "claude-code"],
        cwd=workspace,
        capture_output=True,
        input=b"not json{",
        timeout=5.0,
    )
    assert result.returncode == 0, result.stderr.decode()


def test_claude_code_shim_fail_open_on_no_harness(tmp_path: Path) -> None:
    """Claude Code shim: no .harness/ on walk-up → exit 0 (Axiom 1)."""
    stdin = '{"tool_name":"Edit","tool_input":{"file_path":"a.py"}}'
    result = subprocess.run(
        ["super-harness-hook", "--agent", "claude-code"],
        cwd=tmp_path,
        capture_output=True,
        input=stdin.encode(),
        timeout=5.0,
    )
    assert result.returncode == 0, result.stderr.decode()


def test_unknown_agent_fails_open(tmp_path: Path) -> None:
    """An --agent we don't understand → fail-open ALLOW (exit 0), never block."""
    result = subprocess.run(
        ["super-harness-hook", "--agent", "nonsense-agent"],
        cwd=tmp_path,
        capture_output=True,
        input=b"{}",
        timeout=5.0,
    )
    assert result.returncode == 0, result.stderr.decode()
