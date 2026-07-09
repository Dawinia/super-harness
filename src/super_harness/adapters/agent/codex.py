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
    merge_pre_tool_use_hook,
    merge_session_start_hook,
    merge_stop_hook,
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

super-harness does NOT review for you — it enforces (via the gate) that the
configured number of independent reviewer-source verdicts is recorded before the
lifecycle proceeds, and YOU produce those verdicts. Run `super-harness status
<change>` to see the required reviewer, strategy, and independent-source progress,
then record verdicts with `super-harness review approve/reject <change>
--reviewer <name> [--source <source>]`. Reviewer sources are configured labels in
`.harness/policy.yaml`; super-harness validates them but never executes reviewer
commands itself. When `status` or the prepared bundle shows a source profile,
follow its `agent`, `context`, and agent-specific `agent_options`; do not infer a
global effort/mode vocabulary across Codex, subagent runners, and humans. If the
profile says `context: bundle-only` or `context: incremental`, keep the review
scoped to that bundle or latest delta unless the profile or human reviewer asks
for `full-change`. Code-reviewer approval requires a `--verdict-file` from a
genuinely independent reviewer; run real independent review — don't
self-rubber-stamp.

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

        hooks_path = workspace / ".codex" / "hooks.json"
        pre_command = f"{resolved_hook} --agent codex"
        session_command = f"{resolved_cli} change resume"
        stop_command = f"{resolved_hook} --agent codex --event stop"

        snapshot: str | None = (
            hooks_path.read_text(encoding="utf-8") if hooks_path.exists() else None
        )
        try:
            merge_pre_tool_use_hook(
                hooks_path, command=pre_command,
                matcher=_CODEX_MATCHER, marker=_CODEX_MARKER,
            )
            merge_session_start_hook(hooks_path, command=session_command)
            merge_stop_hook(hooks_path, command=stop_command, marker=_CODEX_STOP_MARKER)
        except BaseException:
            self._restore_snapshot(hooks_path, snapshot)
            raise

    @staticmethod
    def _restore_snapshot(hooks_path: Path, snapshot: str | None) -> None:
        if snapshot is None:
            hooks_path.unlink(missing_ok=True)
        else:
            hooks_path.write_text(snapshot, encoding="utf-8")

    def inject_context(self, change_id: str) -> str:
        result = subprocess.run(
            [_CLI_BINARY, "change", "resume", change_id],
            capture_output=True, text=True, check=False,
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
        backups = sorted(
            hooks_path.parent.glob(f"{hooks_path.name}.super-harness-backup.*"),
            key=_backup_sort_key,
        )
        if not backups:
            return
        hooks_path.write_text(backups[0].read_text(encoding="utf-8"), encoding="utf-8")


def _backup_sort_key(path: Path) -> int:
    suffix = path.name.rsplit(".", 1)[-1]
    try:
        return int(suffix)
    except ValueError:
        return -1
