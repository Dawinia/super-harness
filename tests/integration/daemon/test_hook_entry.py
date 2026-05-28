"""End-to-end tests for the `super-harness-hook` binary per
daemon-architecture §3.5 + cli-command-surface §3.1.

These tests invoke the actual entry-point via `subprocess.run` (the
real PreToolUse path), not the Python function — that's the only way
to verify the click-less import chain stays click-less + the entry-point
is registered in pyproject.toml. Tests skip if the binary isn't on PATH.
"""
from __future__ import annotations

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
    """Use the supervisor to ensure daemon is up (foreground / blocking)."""
    from super_harness.daemon import supervisor
    supervisor.ensure_running(workspace, wait_seconds=5.0)


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
