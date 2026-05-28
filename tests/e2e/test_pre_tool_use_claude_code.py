"""E2E: the pre-tool-use gate blocks/allows Edit via the Claude Code path.

This is the payoff test for Phase 5. It drives the **production** entry points
(subprocesses the installed ``super-harness`` / ``super-harness-daemon`` /
``super-harness-hook`` binaries — they must be on PATH) end-to-end:

  1. ``super-harness init``                  → scaffolds a real ``.harness/``
  2. ``super-harness adapter install claude-code``
                                             → registers the PreToolUse gate hook
                                                in ``.claude/settings.json``
  3. ``super-harness daemon start``          → brings up the gate decision engine
  4. the **exact command string** registered in ``.claude/settings.json`` is
     invoked with a Claude-Code-shaped JSON payload on stdin, and we assert it
     BLOCKS (exit 2) in a blocking state (``INTENT_DECLARED``) and ALLOWS
     (exit 0) once the state advances (``PLAN_APPROVED``).

Why exit 2 (not a JSON ``deny`` decision): Claude Code treats a PreToolUse
hook's exit 2 as a hard block (stderr is fed back to the model) and exit 1 as a
*non-blocking* error (the tool would still proceed). The ``--agent claude-code``
shim therefore exits 2 on block. The hook also **fail-opens to ALLOW when the
daemon is down**, so the daemon MUST be up for a deterministic BLOCK — hence
step 3 and the teardown in ``finally``.

MANUAL SCOPE NOTE (plan Step 2): that exit-2 *actually* halts an ``Edit`` inside
a live Claude Code session is verified MANUALLY by a human in a real CC session
(it needs a live agent + a specific CC version) and is intentionally OUT OF
SCOPE for this automated test. This test proves the binary contract — that the
registered command emits exit 2 then exit 0 across the state flip — which is the
mechanism CC's exit-2-blocks behaviour relies on (we use exit 2, not JSON-deny,
precisely because of the upstream Edit-deny caveat above).
"""
from __future__ import annotations

import json
import shlex
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from super_harness.core.state import ChangeState
from super_harness.core.state_yaml import write_state_yaml

# Claude-Code-shaped PreToolUse payload for an Edit on a source file. The shim
# reads `tool_name` + `tool_input.file_path` from stdin (Claude Code delivers
# PreToolUse input as JSON on stdin, not argv).
_PAYLOAD = json.dumps(
    {
        "hook_event_name": "PreToolUse",
        "tool_name": "Edit",
        "tool_input": {"file_path": "src/foo.py"},
    }
)


def _registered_command(ws: Path) -> str:
    """Extract the super-harness PreToolUse hook command from settings.json.

    Asserting on the EXACT registered string (rather than reconstructing it)
    proves the adapter wrote a runnable hook and that we invoke precisely what
    Claude Code would.
    """
    data = json.loads((ws / ".claude" / "settings.json").read_text())
    for entry in data["hooks"]["PreToolUse"]:
        for hook in entry["hooks"]:
            if "--agent claude-code" in hook["command"]:
                command: str = hook["command"]
                return command
    raise AssertionError("super-harness PreToolUse hook not registered in settings.json")


def _wait_for_returncode(
    run: Callable[[], subprocess.CompletedProcess[str]],
    expected: int,
    *,
    timeout: float = 3.0,
    interval: float = 0.05,
) -> int:
    """Poll ``run()`` until it returns ``expected`` or the timeout elapses.

    Absorbs the HotState mtime-reload race: the daemon caches state.yaml and
    re-parses only when the file's mtime advances, so immediately after a
    ``write_state_yaml`` the daemon's next gate query may still observe the
    previous state for a few milliseconds. Polling (rather than a fixed sleep)
    flips deterministically the instant the daemon picks up the new mtime.
    Returns the last observed returncode (the matched one on success, or the
    final attempt's code on timeout so the caller's assert message is honest).
    """
    deadline = time.monotonic() + timeout
    last = run().returncode
    while last != expected and time.monotonic() < deadline:
        time.sleep(interval)
        last = run().returncode
    return last


def test_pre_tool_use_blocks_then_allows(tmp_path: Path) -> None:
    ws = tmp_path

    # 1. Real super-harness workspace (creates .harness/ + skeleton files).
    subprocess.run(
        ["super-harness", "--workspace", str(ws), "init"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert (ws / ".harness").is_dir(), "init did not create .harness/"

    # 2. Install the claude-code adapter (registers the hook in settings.json).
    subprocess.run(
        ["super-harness", "--workspace", str(ws), "adapter", "install", "claude-code"],
        check=True,
        capture_output=True,
        text=True,
    )
    cmd = _registered_command(ws)

    state_path = ws / ".harness" / "state.yaml"

    def set_state(name: str) -> None:
        write_state_yaml(
            state_path,
            {"c1": ChangeState(change_id="c1", current_state=name)},
            last_reduced_event_id="ev_x",
        )

    def run_hook() -> subprocess.CompletedProcess[str]:
        # Invoke the EXACT registered command with the Claude-Code JSON payload
        # on stdin, from inside the workspace so the hook walks up to .harness/.
        return subprocess.run(
            shlex.split(cmd),
            input=_PAYLOAD,
            text=True,
            capture_output=True,
            cwd=ws,
        )

    # 3. Daemon up so the gate decides for real (not fail-open ALLOW).
    subprocess.run(
        ["super-harness", "--workspace", str(ws), "daemon", "start"],
        check=True,
        capture_output=True,
        text=True,
    )
    try:
        # Blocking state → exit 2 (Claude Code BLOCK).
        set_state("INTENT_DECLARED")
        assert _wait_for_returncode(run_hook, 2) == 2, (
            "expected BLOCK (exit 2) in INTENT_DECLARED; "
            "is the daemon up (else it fail-opens to ALLOW)?"
        )

        # Advance to an allowing state → exit 0 (ALLOW).
        set_state("PLAN_APPROVED")
        assert _wait_for_returncode(run_hook, 0) == 0, (
            "expected ALLOW (exit 0) in PLAN_APPROVED"
        )
    finally:
        subprocess.run(
            ["super-harness", "--workspace", str(ws), "daemon", "stop"],
            capture_output=True,
            text=True,
        )
