"""Click-less PreToolUse hook entry-point.

Registered in pyproject.toml `[project.scripts]` as `super-harness-hook`.
PreToolUse hook script invokes this binary; the click-less import chain
saves ~10ms of cold-start cost vs invoking `super-harness gate check`
(per sensor-gate-architecture §3.6 #5 / daemon-architecture §3.5).

Two invocation modes share ONE decision core (`_decide`); only input
parsing + the block exit-code differ:

  1. Positional (default — generic / shell PreToolUse scripts):
        super-harness-hook <tool> [file]
            argv[0] = tool name (e.g. "Edit", "Write")
            argv[1] = file path (optional; some tools have no file argument)
     Exit codes: 0 = ALLOW, 1 = BLOCK (reason on stderr).

  2. Claude Code shim:
        super-harness-hook --agent claude-code
     Reads a JSON object from STDIN (Claude Code delivers PreToolUse input
     as JSON on stdin, not argv) with keys `tool_name` + `tool_input.file_path`.
     Exit codes: 0 = ALLOW, **2 = BLOCK**. Claude Code treats exit 2 as a
     block (stderr is fed back to the model) and exit 1 as a NON-blocking
     error (the tool would proceed!) — so the shim MUST exit 2, not 1, on
     block, else blocking silently fails open.

env SUPER_HARNESS_CHANGE_ID  optional override (both modes); default derives
                              the active (first non-terminal) change from the
                              `changes` map in .harness/state.yaml.

Fail-open everywhere (Axiom 1: prevent, don't punish — never block on a call
shape we don't understand): empty argv, no .harness/, daemon down, malformed
stdin, and an unknown --agent ALL ALLOW.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Literal

from super_harness.core.active_change import read_active_change_id
from super_harness.core.paths import HarnessNotInitialized, find_harness_root


def main() -> None:  # console_script entry
    argv = sys.argv[1:]
    if argv[:1] == ["--agent"]:
        agent = argv[1] if len(argv) > 1 else ""
        if agent == "claude-code":
            _run_claude_code_shim()
            return
        # Fail-open: never block on a shim contract we don't understand.
        sys.stderr.write(f"super-harness-hook: unknown --agent {agent!r}\n")
        sys.exit(0)
    _run_positional(argv)


def _run_positional(argv: list[str]) -> None:
    """Generic positional mode: exit 0 = ALLOW, exit 1 = BLOCK."""
    if not argv:
        # No tool name → permissive (Axiom 1: harness must not block tools it
        # doesn't understand).
        sys.exit(0)
    tool = argv[0]
    file = argv[1] if len(argv) > 1 else None

    decision, reason = _decide(tool, file)
    if decision == "block":
        sys.stderr.write(f"super-harness: BLOCK ({reason})\n")
        sys.exit(1)
    sys.exit(0)


def _run_claude_code_shim() -> None:
    """Claude Code mode: stdin JSON in, exit 0 = ALLOW, exit 2 = BLOCK.

    Claude Code feeds PreToolUse input as a JSON object on stdin and treats
    exit 2 (only) as a block. Malformed / non-object stdin fails open.
    """
    import json

    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)  # malformed input → fail-open ALLOW
    if not isinstance(data, dict):
        sys.exit(0)
    tool = data.get("tool_name") or ""
    if not tool:
        sys.exit(0)
    tool_input = data.get("tool_input")
    file = tool_input.get("file_path") if isinstance(tool_input, dict) else None

    decision, reason = _decide(tool, file)
    if decision == "block":
        sys.stderr.write(f"super-harness: BLOCK ({reason})\n")
        sys.exit(2)  # Claude Code: exit 2 = block + stderr → model
    sys.exit(0)


def _decide(tool: str, file: str | None) -> tuple[Literal["allow", "block"], str]:
    """Shared decision core for both invocation modes.

    Resolves the workspace root (ALLOW if no .harness/ is found), resolves the
    active change_id (env override > derived active change), and asks the
    supervisor — which fail-open ALLOWs when the daemon is unreachable. Returns
    `(decision, reason)` where decision is "allow" or "block". Callers map the
    block decision onto the exit code their agent expects (1 positional, 2 for
    Claude Code).
    """
    try:
        root = find_harness_root(Path.cwd())
    except HarnessNotInitialized:
        # No super-harness in this workspace → not our concern; ALLOW.
        return "allow", "no .harness in workspace"

    # Resolve change_id: env override > derived active change > None.
    import os

    change_id = os.environ.get("SUPER_HARNESS_CHANGE_ID")
    if not change_id:
        change_id = _read_active_change_id(root)

    # Late import to keep startup lean — supervisor pulls in client + protocol
    # + subprocess. ~3-5ms on Apple Silicon vs ~12ms for click.
    from super_harness.daemon import supervisor

    return supervisor.gate_pre_tool_use(
        root, tool=tool, file=file, change_id=change_id
    )


def _read_active_change_id(root: Path) -> str | None:
    """Thin delegating alias for `core.active_change.read_active_change_id`.

    Kept as a live callable (the hook's state-resolution is exercised through
    this name by `tests/integration/daemon/test_hook_entry.py`). The shared
    logic now lives in `core.active_change` so the `gate check` CLI resolves
    the same active change.
    """
    return read_active_change_id(root)


if __name__ == "__main__":  # pragma: no cover
    main()
