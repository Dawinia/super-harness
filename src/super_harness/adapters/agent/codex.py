"""AgentAdapter for OpenAI Codex CLI (portability axis B).

Registers a PreToolUse hook (deny via stdout `permissionDecision`) + a
SessionStart hook (stdout = developer context) into `<repo>/.codex/hooks.json`,
reusing the agent-neutral `_settings_merge`. Codex's hooks.json has the same
shape as Claude's settings.json hooks block; only the matcher + marker differ.

Trust caveat: Codex skips new/changed hooks until a human runs `/hooks` to trust
them — the gate is INACTIVE until then. See design 2026-06-25 §4.3.

API stability: experimental (v0.1).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import ClassVar

from super_harness.adapters import AgentAdapter
from super_harness.adapters.agent._settings_merge import (
    merge_pre_tool_use_hook,
    merge_session_start_hook,
)

__all__ = ["CodexAdapter"]

_HOOK_BINARY = "super-harness-hook"
_CLI_BINARY = "super-harness"
_CODEX_MATCHER = "^(apply_patch|Edit|Write)$"
_CODEX_MARKER = "--agent codex"

_AGENTS_MD_BEGIN = "<!-- super-harness agent: codex -->"
_AGENTS_MD_END = "<!-- /super-harness agent: codex -->"
_AGENTS_MD_SUBSECTION = f"""{_AGENTS_MD_BEGIN}
### super-harness (Codex)

A **PreToolUse** hook gates this workspace. `apply_patch` edits are blocked by
super-harness when the current change state forbids the mutation (deterministic
gate enforcement). `Bash` is never gated, so the kill-switch always works.

**REQUIRED trust step:** after `adapter install codex`, the gate is INACTIVE
until you run `/hooks` in Codex and trust the super-harness hook. Codex skips
new/changed hooks until trusted (trust is keyed to the hook's hash); if you
reinstall or relocate the binary, re-trust it. On a pre-existing repo also run
`super-harness sync --gitignore` so `.codex/hooks.json` is ignored.

**Coverage caveat:** Codex PreToolUse intercepts only simple shell + `apply_patch`
— it does NOT see `WebSearch` or other non-shell/non-MCP tools, so real-time
coverage is narrower than Claude Code's. The CI cold floor backs the gap.

When a tool call is blocked:
- Run `super-harness status` to see the change, its state, and the next step.
- Resume context with `super-harness change resume <change_id>`.
- If the gate blocks a tool call, stop and surface the block plus the next valid
  step (`super-harness status`) to the human. Do **not** touch the kill switch
  (the emergency override file under `.harness/`) yourself — it is a **human-only**
  emergency override; an agent using it to get past a block defeats the gate, and
  any such bypass is recorded and disclosed at the merge gate. Whether to override
  is the human's call.

#### Review protocol

super-harness does NOT review for you — it enforces (via the gate) that a review
verdict is recorded before the lifecycle proceeds, and YOU produce the verdict.
Run `super-harness status <change>` to see the required reviewer + strategy, then
record verdicts with `super-harness review approve/reject <change> --reviewer
<name>` (code-reviewer approval requires a `--verdict-file` from a genuinely
independent reviewer subagent; see `super-harness status` output). Run a real
independent reviewer — don't self-rubber-stamp.
{_AGENTS_MD_END}"""


class CodexAdapter(AgentAdapter):
    name: ClassVar[str] = "codex"
    version: ClassVar[str] = "0.1.0"
    capabilities: ClassVar[dict[str, bool]] = {
        "pre_tool_use_hook": True,
        "post_tool_use_hook": False,
        "session_start_hook": True,
        "session_end_hook": False,
        "pre_commit_hook": False,
        "rules_file_injection": True,
        "mcp_server": True,
        "subprocess_execution": True,
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

        snapshot: str | None = hooks_path.read_text() if hooks_path.exists() else None
        try:
            merge_pre_tool_use_hook(
                hooks_path, command=pre_command,
                matcher=_CODEX_MATCHER, marker=_CODEX_MARKER,
            )
            merge_session_start_hook(hooks_path, command=session_command)
        except BaseException:
            self._restore_snapshot(hooks_path, snapshot)
            raise

    @staticmethod
    def _restore_snapshot(hooks_path: Path, snapshot: str | None) -> None:
        if snapshot is None:
            hooks_path.unlink(missing_ok=True)
        else:
            hooks_path.write_text(snapshot)

    def inject_context(self, change_id: str) -> str:
        result = subprocess.run(
            [_CLI_BINARY, "change", "resume", change_id],
            capture_output=True, text=True, check=False,
        )
        return result.stdout or ""

    def agents_md_subsection(self) -> str:
        return _AGENTS_MD_SUBSECTION

    def local_config_relpath(self) -> str:
        return ".codex/hooks.json"

    def installed_detail(self) -> str:
        return (
            "PreToolUse + SessionStart hooks registered in .codex/hooks.json — "
            "run `/hooks` in Codex to trust the hook before the gate is active"
        )

    def on_uninstall(self, workspace: Path) -> None:
        hooks_path = workspace / ".codex" / "hooks.json"
        backups = sorted(
            hooks_path.parent.glob(f"{hooks_path.name}.super-harness-backup.*"),
            key=_backup_sort_key,
        )
        if not backups:
            return
        hooks_path.write_text(backups[0].read_text())


def _backup_sort_key(path: Path) -> int:
    suffix = path.name.rsplit(".", 1)[-1]
    try:
        return int(suffix)
    except ValueError:
        return -1
