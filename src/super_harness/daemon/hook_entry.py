"""Click-less PreToolUse hook entry-point.

Registered in pyproject.toml `[project.scripts]` as `super-harness-hook`.
PreToolUse hook script invokes this binary; the click-less import chain
saves ~10ms of cold-start cost vs invoking `super-harness gate check`
(per sensor-gate-architecture §3.6 #5 / daemon-architecture §3.5).

Three invocation modes share ONE decision core (`_decide`); only input
parsing + the block signalling differ:

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

  3. Codex shim:
        super-harness-hook --agent codex
     Reads a JSON object from STDIN (Codex delivers PreToolUse input as JSON
     on stdin) with keys `tool_name` + `tool_input.command`. Codex blocks via
     a stdout JSON `hookSpecificOutput.permissionDecision: "deny"` (the
     decision lives in the JSON, NOT the exit code) — so this shim ALWAYS
     exits 0 and prints the deny object on block, nothing on allow. Codex
     gives a `command`, not a `file_path`, so the gate decides on lifecycle
     state with `file=None`.

env SUPER_HARNESS_CHANGE_ID  optional override (both modes); default derives
                              the active (most recently active non-terminal) change
                              from the `changes` map in .harness/state.yaml.

Fail-open everywhere (Axiom 1: prevent, don't punish — never block on a call
shape we don't understand): empty argv, no .harness/, daemon down, malformed
stdin, and an unknown --agent ALL ALLOW.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from super_harness.adapters import AgentAdapter

from super_harness.core.active_change import read_active_change_id
from super_harness.core.paths import HarnessNotInitialized, find_harness_root

_HALT_HINT = (
    "Stop and tell the human — run `super-harness status` for the next valid step. "
    "Do NOT bypass the gate yourself."
)


def main() -> None:  # console_script entry
    argv = sys.argv[1:]
    # Optional `--event <name>` selects the hook event (default: pre-tool-use). Strip it
    # first so the existing `--agent` dispatch is unchanged when it is absent.
    event = "pre-tool-use"
    if "--event" in argv:
        i = argv.index("--event")
        event = argv[i + 1] if i + 1 < len(argv) else event
        argv = argv[:i] + argv[i + 2 :]
    if argv[:1] == ["--agent"]:
        agent = argv[1] if len(argv) > 1 else ""
        if agent == "claude-code":
            if event == "stop":
                from super_harness.adapters.agent.claude_code import ClaudeCodeAdapter
                _run_stop(ClaudeCodeAdapter())
            else:
                _run_claude_code_shim()
            return
        if agent == "codex":
            if event == "stop":
                from super_harness.adapters.agent.codex import CodexAdapter
                _run_stop(CodexAdapter())
            else:
                _run_codex_shim()
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
        sys.stderr.write(f"super-harness: BLOCK ({reason}). {_HALT_HINT}\n")
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
        sys.stderr.write(f"super-harness: BLOCK ({reason}). {_HALT_HINT}\n")
        sys.exit(2)  # Claude Code: exit 2 = block + stderr → model
    sys.exit(0)


def _run_codex_shim() -> None:
    """Codex mode: stdin JSON in; deny via stdout JSON `permissionDecision`.

    Codex feeds PreToolUse input as a JSON object on stdin (`tool_name`,
    `tool_input.command`) and treats `hookSpecificOutput.permissionDecision:
    "deny"` printed on stdout as a block (the decision lives in the JSON, not the
    exit code — so we exit 0). Malformed / non-object / missing tool → fail-open
    ALLOW (exit 0, no output). Codex gives a `command`, not a `file_path`, so the
    gate decides on lifecycle state with `file=None`.
    """
    import json

    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)
    if not isinstance(data, dict):
        sys.exit(0)
    tool = data.get("tool_name") or ""
    if not tool:
        sys.exit(0)

    decision, reason = _decide(tool, None)
    if decision == "block":
        json.dump(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        f"super-harness: BLOCK ({reason}). {_HALT_HINT}"
                    ),
                }
            },
            sys.stdout,
        )
        sys.exit(0)
    sys.exit(0)


def _run_stop(adapter: AgentAdapter) -> None:
    """Agent-agnostic turn-end (Stop) authoring-check orchestrator. ALWAYS exits 0 (the
    turn's edits stand). The adapter owns the agent-specific Stop protocol: the
    re-entrancy guard (`adapter.stop_should_check`) and the feedback envelope
    (`adapter.format_stop_feedback`). This function contains NO agent field names.
    Fail-open on any error / no harness / kill switch (Axiom 1 — never let the check
    break the agent)."""
    import json

    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)
    if not isinstance(data, dict):
        sys.exit(0)
    try:
        root = find_harness_root(Path.cwd())
    except HarnessNotInitialized:
        sys.exit(0)
    if (root / ".harness" / "gate-disabled").exists():
        sys.exit(0)  # kill switch → allow
    try:
        # Inside the try so a buggy adapter guard fails open (allow), not break the agent.
        if not adapter.stop_should_check(data):
            sys.exit(0)  # continuation turn (or adapter opts out) → allow
        from super_harness.core.authoring_check import run_authoring_check

        verdict = run_authoring_check(root)
        out = adapter.format_stop_feedback(verdict)
    except Exception:
        sys.exit(0)  # fail-open: never let the check break the agent
    if out:
        sys.stdout.write(out)
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

    # File-based kill switch (self-host hard-gate escape hatch): a sentinel file
    # short-circuits to ALLOW before any daemon/state access, so a wedged daemon
    # or corrupt state can never trap the user. Toggle via ungated Bash
    # (`touch .harness/gate-disabled` / `rm`). Robust where `daemon stop` is not
    # — the unreachable path respawns the daemon, so stop only reprieves one edit.
    if (root / ".harness" / "gate-disabled").exists():
        _record_bypass(root, tool=tool, file=file)
        return "allow", "gate disabled (.harness/gate-disabled present)"

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


def _record_bypass(root: Path, *, tool: str, file: str | None) -> None:
    """Best-effort record a `gate_bypassed` audit event. NEVER raises — recording
    must not break the safety path. Skips when no active change (a bypass with no
    change has no merge gate to disclose at; design §4)."""
    try:
        import os

        from super_harness.core.clock import utc_now_iso
        from super_harness.core.events import Actor, Event
        from super_harness.core.paths import events_path
        from super_harness.core.ulid import new_event_id
        from super_harness.core.writer import EventWriter

        change_id = os.environ.get("SUPER_HARNESS_CHANGE_ID") or _read_active_change_id(root)
        if not change_id:
            return
        ev = Event(
            event_id=new_event_id(),
            type="gate_bypassed",
            change_id=change_id,
            timestamp=utc_now_iso(),
            actor=Actor(type="sensor", identifier="gate"),
            framework="plain",
            payload={"tool": tool, "file": file or ""},
        )
        EventWriter(events_path(root)).emit(ev, skip_validation=True)
    except Exception:
        pass


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
