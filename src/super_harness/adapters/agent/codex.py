"""AgentAdapter for OpenAI Codex CLI (portability axis B).

Registers a PreToolUse hook (deny via stdout `permissionDecision`), a
SessionStart hook (stdout = developer context), and a Stop hook (turn-end
authoring-conformance advisory) into `<repo>/.codex/hooks.json`, reusing the
agent-neutral `_settings_merge`. Codex's hooks.json has the same shape as
Claude's settings.json hooks block; only the matcher + marker differ.

Trust caveat: Codex skips new/changed hooks until a human runs `/hooks` to trust
them — the gate is INACTIVE until then. See design 2026-06-25 §4.3.

API stability: experimental (v0.1).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from super_harness.adapters import AgentAdapter
from super_harness.adapters.agent import _stop_protocol
from super_harness.adapters.agent._settings_merge import (
    SettingsMergePlan,
    apply_settings_merge_plan,
    plan_settings_merge,
    restore_or_remove_managed_hooks,
)

if TYPE_CHECKING:
    from super_harness.core.authoring_check import Verdict

__all__ = ["CodexAdapter"]

_HOOK_BINARY = "super-harness-hook"
_CLI_BINARY = "super-harness"
_CODEX_MATCHER = "^(apply_patch|Edit|Write)$"
_CODEX_MARKER = "--agent codex"
# Codex-specific Stop marker: merge_stop_hook defaults to the CLAUDE pair
# (`_STOP_OURS_MARKER`), so passing this explicitly is what makes a Codex reinstall
# REPLACE the prior Stop entry instead of appending a duplicate (two objects on stdout
# → Codex "Stop Failed"). Mirrors the PreToolUse `_CODEX_MARKER` pattern.
_CODEX_STOP_MARKER = "--agent codex --event stop"

_AGENTS_MD_BEGIN = "<!-- super-harness agent: codex -->"
_AGENTS_MD_END = "<!-- /super-harness agent: codex -->"
_AGENTS_MD_SUBSECTION = f"""{_AGENTS_MD_BEGIN}
### super-harness (Codex)

A **PreToolUse** hook gates this workspace. `apply_patch` edits are blocked by
super-harness when the current change state forbids the mutation (deterministic
gate enforcement). `Bash` is never gated (see the coverage caveat below).

**REQUIRED trust step:** after `adapter install codex`, the gate is INACTIVE
until you run `/hooks` in Codex and trust the super-harness hook. Codex skips
new/changed hooks until trusted (trust is keyed to the hook's hash); if you
reinstall or relocate the binary, re-trust it. On a pre-existing repo also run
`super-harness sync --gitignore` so `.codex/hooks.json` is ignored.

**Coverage caveat:** Codex surfaces only shell commands and `apply_patch` to
PreToolUse hooks — it does NOT expose `WebSearch` or other non-shell/non-MCP
tools. super-harness gates `apply_patch` edits (never `Bash`, so the kill-switch
keeps working); an action taken through a tool Codex does not surface isn't caught
in real time, so real-time coverage is narrower than Claude Code's. The CI cold
floor backs the gap.

When a tool call is blocked:
- Run `super-harness status` to see the change, its state, and the next step.
- Resume context with `super-harness change resume <change_id>`.
- If the gate blocks a tool call, stop and surface the block plus the next valid
  step (`super-harness status`) to the human. Do **not** try to disable or work
  around the gate yourself — overriding it is a **human-only** decision, and any
  bypass is recorded and disclosed at the merge gate. Whether to override is the
  human's call.

#### Review protocol

super-harness does NOT start, spawn, or host reviewers. It compiles immutable
contracts and records independent receipts. Tracked project requirements live in
`.harness/review-governance.yaml`; each user's explicit models and producer options
live in the gitignored `.harness/review-profiles.local.yaml`. Do not assume a Codex
agent can spawn another agent, and do not substitute an in-session self-review for
an external or human source.

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

#### Turn-end authoring check

A **Stop** hook runs a turn-end authoring-time conformance check: when you finish a
turn, any ratified decision that opted in (`authoring_time: true`) has its check run
once; a failure is fed back as a non-blocking advisory so you self-correct next turn.
Like the PreToolUse gate, the Stop hook is INACTIVE until you `/hooks`-trust it.
{_AGENTS_MD_END}"""


class CodexAdapter(AgentAdapter):
    name: ClassVar[str] = "codex"
    version: ClassVar[str] = "0.1.0"
    capabilities: ClassVar[dict[str, bool]] = {
        "pre_tool_use_hook": True,
        "post_tool_use_hook": True,  # spike-verified: fires under `codex exec` (2026-07-01)
        "session_start_hook": True,
        "session_end_hook": False,
        "pre_commit_hook": False,
        "rules_file_injection": True,
        "mcp_server": True,
        "subprocess_execution": True,
        "turn_end_feedback_hook": True,  # Codex Stop hook (cut-2)
    }

    def detect(self, workspace: Path) -> bool:
        return (workspace / ".codex").is_dir()

    def install_hooks(self, workspace: Path) -> None:
        resolved_hook = shutil.which(_HOOK_BINARY)
        if resolved_hook is None:
            raise RuntimeError(
                f"{_HOOK_BINARY} not found on PATH; reinstall super-harness "
                f"(e.g. `pipx reinstall super-harness`) before installing the "
                f"Codex adapter."
            )
        resolved_cli = shutil.which(_CLI_BINARY)
        if resolved_cli is None:
            raise RuntimeError(
                f"{_CLI_BINARY} not found on PATH; reinstall super-harness "
                f"(e.g. `pipx reinstall super-harness`) before installing the "
                f"Codex adapter."
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
            workspace / ".codex" / "hooks.json",
            workspace_root=workspace,
            pre_tool_use_command=f"{hook_executable} --agent codex",
            session_start_command=f"{cli_executable} change resume",
            stop_command=f"{hook_executable} --agent codex --event stop",
            pre_tool_use_matcher=_CODEX_MATCHER,
            pre_tool_use_marker=_CODEX_MARKER,
            stop_marker=_CODEX_STOP_MARKER,
        )

    def inject_context(self, change_id: str) -> str:
        result = subprocess.run(
            [_CLI_BINARY, "change", "resume", change_id],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.stdout or ""

    def stop_should_check(self, payload: dict[str, Any]) -> bool:
        """Skip the continuation turn a prior block created (loop-safety). Codex's Stop
        payload carries `stop_hook_active`, spiked identical to Claude Code's."""
        return not _stop_protocol.is_continuation(payload)

    def format_stop_feedback(self, verdict: Verdict) -> str:
        """Codex Stop feedback = the shared Claude-Code-hook family envelope
        (`{"decision":"block","reason": ...}`). Spike-verified under `codex exec`:
        `reason` is the ONLY channel that reaches the model; adding systemMessage /
        additionalContext makes Codex report "Stop Failed" and drop the continuation."""
        return _stop_protocol.block_feedback(verdict)

    def agents_md_subsection(self) -> str:
        return _AGENTS_MD_SUBSECTION

    def local_config_relpath(self) -> str:
        return ".codex/hooks.json"

    def installed_detail(self) -> str:
        return (
            "PreToolUse + SessionStart + Stop hooks registered in .codex/hooks.json — "
            "run `/hooks` in Codex to trust the hooks before the gate is active"
        )

    def on_uninstall(self, workspace: Path) -> None:
        hooks_path = workspace / ".codex" / "hooks.json"
        restore_or_remove_managed_hooks(
            hooks_path,
            pre_tool_use_marker=_CODEX_MARKER,
            stop_marker=_CODEX_STOP_MARKER,
            workspace_root=workspace,
        )
