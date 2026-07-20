"""Reference ``AgentAdapter`` for Claude Code (adapter-architecture §3.5).

This is the canonical adapter super-harness ships in v0.1. It bridges Claude
Code's runtime to the harness by registering three hooks directly into
``.claude/settings.local.json`` (the per-machine, conventionally-gitignored
settings file — NEVER the committed shared ``settings.json`` — because the hook
``command`` pins a machine-specific absolute path):

- a **PreToolUse** hook whose ``command`` points at the ``super-harness-hook``
  binary (no ``.sh`` wrapper; input parsing lives inside that binary, see
  sensor-gate §3.2.1 + daemon §3.5), and
- a **SessionStart** hook whose ``command`` is ``<super-harness> change resume``
  (no slug → active change): Claude Code injects its stdout as session context.

Scope deltas baked in per spec §3.5:
- **DELTA 2026-05-28 (Phase 5)**: no ``.sh`` script is written — the hook
  ``command`` is the resolved binary path directly.
- **SessionStart wired (Phase 7)**: powered by ``change resume``'s no-arg
  "resolve active change" mode (``core.active_change.read_active_change_id``).

This module only returns the AGENTS.md subsection CONTENT
(``agents_md_subsection``); the actual injection is wired in ``cli/init.py``
(outer section + no-agent anchor) and ``cli/adapter.py`` (install injects the
subsection / uninstall removes it).

API stability: **experimental** (v0.1).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from super_harness.adapters import AgentAdapter
from super_harness.adapters.agent import _stop_protocol

if TYPE_CHECKING:
    from super_harness.core.authoring_check import Verdict
from super_harness.adapters.agent._settings_merge import (
    SettingsMergePlan,
    apply_settings_merge_plan,
    plan_settings_merge,
    restore_or_remove_managed_hooks,
)

__all__ = [
    "ClaudeCodeAdapter",
]

# The binary the PreToolUse hook invokes. A click-less entry point that imports
# the gate directly to skip the ~10ms click-group dispatch cold start (daemon
# §3.5). Resolved to an absolute path at install time via shutil.which.
_HOOK_BINARY = "super-harness-hook"
# The user-facing entry point `inject_context` shells out to for `change resume`.
_CLI_BINARY = "super-harness"

# Static AGENTS.md subsection content (this module returns the string; the
# injection is wired in cli/init.py + cli/adapter.py). Wrapped in markers so the
# injector can locate + replace this block idempotently.
_AGENTS_MD_BEGIN = "<!-- super-harness agent: claude-code -->"
_AGENTS_MD_END = "<!-- /super-harness agent: claude-code -->"
_AGENTS_MD_SUBSECTION = f"""{_AGENTS_MD_BEGIN}
### super-harness (Claude Code)

A **PreToolUse** hook is enabled for this workspace. `Edit` / `Write` /
`MultiEdit` / `NotebookEdit` tool calls are blocked by super-harness when the
current change state forbids the mutation (deterministic gate enforcement).

A **Stop** hook also runs a turn-end authoring-time conformance check: when you
finish a turn, any ratified decision that opted in (`authoring_time: true`) has its
check run once, and a failing check blocks the stop with a **non-blocking advisory**
naming the violated decision — so you can self-correct before the merge gate. It never
undoes your edit and never blocks twice (it nudges once per turn); the merge gate is
the authoritative floor.

When a tool call is blocked by the gate:
- Run `super-harness status` to see the current change, its state, and why the
  edit was rejected, plus the next valid step.
- Resume context for a change with `super-harness change resume <change_id>`.
- **Revising a rejected plan is authorized in-gate:** in `PLAN_REJECTED`, editing
  the change's own recorded plan document (a marked `.md` in the declared scope) is
  ALLOWED through the normal `Edit`/`Write` tools — that is the intended reject-loop
  path, not a bypass. Source files stay blocked. Do not write plan revisions through
  the shell to dodge the gate.
- **If a tool call is blocked by the gate:** stop, and surface the block plus the
  next valid step (`super-harness status`) to the human. Do **not** try to disable
  or work around the gate yourself — overriding it is a **human-only** decision, and
  any bypass is recorded and disclosed at the merge gate. Whether to override is the
  human's call.

#### Review protocol

super-harness does NOT start, spawn, or host reviewers. It compiles immutable
contracts and records independent receipts. Tracked project requirements live in
`.harness/review-governance.yaml`; each user's explicit models and producer options
live in the gitignored `.harness/review-profiles.local.yaml`. Do not assume a Claude
Code `Task` subagent is the review protocol, and do not substitute an in-session
self-review for an external or human source.

For each review epoch:

1. Commit the exact in-scope change, then run `super-harness review prepare
   <change> --reviewer <name>` once.
2. Run `super-harness review begin <change> --reviewer <name>` to freeze the
   automated round. The command returns per-run prompt, schema, output, and
   invocation files; it never invokes the producer.
3. The caller runs every issued invocation outside super-harness, unchanged and in
   listed order. Apply the source's explicit model and agent-specific options
   verbatim. Do not edit while any issued run is pending.
4. Import each completed output with `super-harness review result import ...`; if a
   producer crashes, record it once with `super-harness review run fail ...`.
   Collect every source before responding to findings, even if one reports a
   blocker. Then batch the fixes and prepare one follow-up round.

The frozen inspection target is strict: findings may address only its exact range
and files. A reviewer may read unchanged repository material as supporting context.
It must continue the whole target after finding a blocker. If the target itself is
insufficient, return `scope_sufficient: false` with a finding; never widen it to the
whole PR ad hoc. A code-only finding fix does not trigger plan review unless the
approved plan, scope, or requirements changed; use `plan redeclare` when they did.

Human review is first-class: use `review human inspect`, validate a verdict with
`review human draft`, then leave `review human confirm` to a human in a TTY. An
agent must never confirm the human nonce. `review skip` remains a disclosed escape
hatch; a code-review skip needs an explicit override and reason to pass attestation.
{_AGENTS_MD_END}"""


class ClaudeCodeAdapter(AgentAdapter):
    """Reference adapter for the Claude Code runtime (adapter-architecture §3.5).

    Registers a PreToolUse gate hook into ``.claude/settings.local.json`` and
    resumes change context via the ``super-harness change resume`` CLI.
    """

    name: ClassVar[str] = "claude-code"
    version: ClassVar[str] = "0.1.0"
    capabilities: ClassVar[dict[str, bool]] = {
        "pre_tool_use_hook": True,  # Claude Code PreToolUse hook
        "post_tool_use_hook": True,  # Claude Code PostToolUse hook
        "session_start_hook": True,  # Claude Code SessionStart hook (Phase 7)
        "session_end_hook": False,  # Claude Code has no explicit session-end hook
        "pre_commit_hook": False,  # commit is user-driven git, not an agent hook
        "rules_file_injection": True,  # CLAUDE.md / AGENTS.md
        "mcp_server": True,  # v0.2 strengthens context injection via MCP
        "subprocess_execution": True,  # Bash tool
        "turn_end_feedback_hook": True,  # Claude Code Stop hook (cut-1)
    }

    def detect(self, workspace: Path) -> bool:
        """A workspace uses Claude Code iff it has a ``.claude/`` directory."""
        return (workspace / ".claude").is_dir()

    def install_hooks(self, workspace: Path) -> None:
        """Register the super-harness PreToolUse + SessionStart hooks.

        Resolves both management binaries to absolute paths up front, plans all
        three hook entries as one settings transaction, then applies that plan
        with at most one pristine backup. The hook
        ``command`` pins a machine-specific absolute path, so it belongs in the
        per-machine, conventionally-gitignored ``settings.local.json`` — never
        the committed shared ``settings.json``:

        - **PreToolUse**: ``command`` = ``<abs super-harness-hook> --agent
          claude-code`` (deterministic gate enforcement).
        - **SessionStart**: ``command`` = ``<abs super-harness> change resume``
          (no slug → active change); Claude Code injects its stdout as context.

        Per spec §3.5 this writes NO ``.sh`` script.

        Raises:
            RuntimeError: if ``super-harness-hook`` or ``super-harness`` is not
                resolvable on PATH (a broken install the user must fix by
                reinstalling) — raised BEFORE any write.
        """
        resolved_hook = shutil.which(_HOOK_BINARY)
        if resolved_hook is None:
            raise RuntimeError(
                f"{_HOOK_BINARY} not found on PATH; reinstall super-harness "
                f"(e.g. `pipx reinstall super-harness`) so the gate hook binary "
                f"is available before installing the Claude Code adapter."
            )
        resolved_cli = shutil.which(_CLI_BINARY)
        if resolved_cli is None:
            raise RuntimeError(
                f"{_CLI_BINARY} not found on PATH; reinstall super-harness "
                f"(e.g. `pipx reinstall super-harness`) so the CLI binary is "
                f"available before installing the Claude Code adapter."
            )

        plan = self.plan_hook_install(
            workspace, hook_executable=resolved_hook, cli_executable=resolved_cli
        )
        assert plan is not None
        apply_settings_merge_plan(plan)

    def plan_hook_install(
        self, workspace: Path, *, hook_executable: str, cli_executable: str
    ) -> SettingsMergePlan:
        return plan_settings_merge(
            workspace / ".claude" / "settings.local.json",
            workspace_root=workspace,
            pre_tool_use_command=f"{hook_executable} --agent claude-code",
            session_start_command=f"{cli_executable} change resume",
            stop_command=f"{hook_executable} --agent claude-code --event stop",
        )

    def stop_should_check(self, payload: dict[str, Any]) -> bool:
        """Skip the continuation turn a prior block created (loop-safety). Delegates to
        the shared Claude-Code-hook family guard (`stop_hook_active`)."""
        return not _stop_protocol.is_continuation(payload)

    def format_stop_feedback(self, verdict: Verdict) -> str:
        """Claude Code Stop feedback = the shared Claude-Code-hook family envelope
        (`{"decision":"block","reason": ...}`; the reason reaches the model on its next
        turn, the edit is never undone). ``""`` when clean, so the hook allows the stop.
        Loop-safety lives in :meth:`stop_should_check` / the hook entry, not here."""
        return _stop_protocol.block_feedback(verdict)

    def inject_context(self, change_id: str) -> str:
        """Return the ``change resume`` context dump for ``change_id``.

        Delegates to ``super-harness change resume <change_id>`` and returns its
        stdout. A non-zero exit or empty stdout (e.g. unknown slug) yields ``""``
        rather than raising — context injection is best-effort.
        """
        # Intentionally shells out to the BARE `super-harness` name (runtime PATH),
        # not the shutil.which-resolved absolute path the SessionStart hook pins at
        # install time. This is a best-effort programmatic call, never the gate
        # path, so it doesn't need the install-time pinned absolute path.
        result = subprocess.run(
            [_CLI_BINARY, "change", "resume", change_id],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.stdout or ""

    def agents_md_subsection(self) -> str:
        """Return the marker-wrapped AGENTS.md subsection for Claude Code.

        Content-only: the actual injection is wired in ``cli/init.py`` (outer
        section + anchor) and ``cli/adapter.py`` (install injects this / uninstall
        removes it). The text teaches the agent that a PreToolUse gate is enforced
        and how to recover from a block (`super-harness status` + `change resume`).
        """
        return _AGENTS_MD_SUBSECTION

    def local_config_relpath(self) -> str:
        return ".claude/settings.local.json"

    def installed_detail(self) -> str:
        return (
            "PreToolUse gate + SessionStart context + Stop authoring-check hooks "
            "registered in .claude/settings.local.json"
        )

    def on_uninstall(self, workspace: Path) -> None:
        """Restore the earliest pristine backup or remove marker-owned hooks."""
        settings_path = workspace / ".claude" / "settings.local.json"
        restore_or_remove_managed_hooks(
            settings_path,
            pre_tool_use_marker="--agent claude-code",
            stop_marker="--agent claude-code --event stop",
            workspace_root=workspace,
        )
