"""Reference ``AgentAdapter`` for Claude Code (adapter-architecture §3.5).

This is the canonical adapter super-harness ships in v0.1. It bridges Claude
Code's runtime to the harness by registering a **PreToolUse** hook directly into
``.claude/settings.json`` — the hook ``command`` points at the
``super-harness-hook`` binary (no ``.sh`` wrapper; input parsing lives inside
that binary, see sensor-gate §3.2.1 + daemon §3.5).

Scope deltas baked in per spec §3.5:
- **DELTA 2026-05-28 (Phase 5)**: no ``.sh`` script is written — the hook
  ``command`` is the resolved binary path directly.
- **SessionStart is DEFERRED to Phase 9**: this adapter wires PreToolUse ONLY.
  SessionStart context injection needs ``change resume`` to grow a no-arg
  "resolve active change" mode (it currently requires a ``<slug>``), which
  lands with the AGENTS.md injection work in Phase 9.

The actual AGENTS.md injection and registry/CLI wiring live in later tasks; this
module only provides the adapter class itself.

API stability: **experimental** (v0.1).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import ClassVar

from super_harness.adapters import AgentAdapter
from super_harness.adapters.agent._settings_merge import merge_pre_tool_use_hook

__all__ = [
    "ClaudeCodeAdapter",
]

# The binary the PreToolUse hook invokes. A click-less entry point that imports
# the gate directly to skip the ~10ms click-group dispatch cold start (daemon
# §3.5). Resolved to an absolute path at install time via shutil.which.
_HOOK_BINARY = "super-harness-hook"
# The user-facing entry point `inject_context` shells out to for `change resume`.
_CLI_BINARY = "super-harness"

# Static AGENTS.md subsection content (Phase 9 does the actual injection; this
# method only returns the string). Wrapped in markers so the future injector can
# locate + replace this block idempotently.
_AGENTS_MD_BEGIN = "<!-- super-harness agent: claude-code -->"
_AGENTS_MD_END = "<!-- /super-harness agent: claude-code -->"
_AGENTS_MD_SUBSECTION = f"""{_AGENTS_MD_BEGIN}
### super-harness (Claude Code)

A **PreToolUse** hook is enabled for this workspace. `Edit` / `Write` /
`MultiEdit` / `NotebookEdit` tool calls are blocked by super-harness when the
current change state forbids the mutation (deterministic gate enforcement).

When a tool call is blocked by the gate:
- Run `super-harness status` to see the current change, its state, and why the
  edit was rejected, plus the next valid step.
- Resume context for a change with `super-harness change resume <change_id>`.
{_AGENTS_MD_END}"""


class ClaudeCodeAdapter(AgentAdapter):
    """Reference adapter for the Claude Code runtime (adapter-architecture §3.5).

    Registers a PreToolUse gate hook into ``.claude/settings.json`` and resumes
    change context via the ``super-harness change resume`` CLI.
    """

    name: ClassVar[str] = "claude-code"
    version: ClassVar[str] = "0.1.0"
    capabilities: ClassVar[dict[str, bool]] = {
        "pre_tool_use_hook": True,  # Claude Code PreToolUse hook
        "post_tool_use_hook": True,  # Claude Code PostToolUse hook
        "session_start_hook": True,  # capability exists; wiring deferred (Phase 9)
        "session_end_hook": False,  # Claude Code has no explicit session-end hook
        "pre_commit_hook": False,  # commit is user-driven git, not an agent hook
        "rules_file_injection": True,  # CLAUDE.md / AGENTS.md
        "mcp_server": True,  # v0.2 strengthens context injection via MCP
        "subprocess_execution": True,  # Bash tool
    }

    def detect(self, workspace: Path) -> bool:
        """A workspace uses Claude Code iff it has a ``.claude/`` directory."""
        return (workspace / ".claude").is_dir()

    def install_hooks(self, workspace: Path) -> None:
        """Register the super-harness PreToolUse hook in ``.claude/settings.json``.

        Resolves the ``super-harness-hook`` binary to an absolute path and merges
        a PreToolUse entry whose ``command`` is ``<abs> --agent claude-code`` into
        the existing settings (no clobber, backed up first, idempotent — see
        ``merge_pre_tool_use_hook``).

        Per spec §3.5 this writes NO ``.sh`` script and does NOT wire SessionStart
        (deferred to Phase 9): PreToolUse only.

        Raises:
            RuntimeError: if ``super-harness-hook`` is not resolvable on PATH
                (a broken install the user must fix by reinstalling).
        """
        resolved = shutil.which(_HOOK_BINARY)
        if resolved is None:
            raise RuntimeError(
                f"{_HOOK_BINARY} not found on PATH; reinstall super-harness "
                f"(e.g. `pipx reinstall super-harness`) so the gate hook binary "
                f"is available before installing the Claude Code adapter."
            )
        command = f"{resolved} --agent claude-code"
        merge_pre_tool_use_hook(
            workspace / ".claude" / "settings.json", command=command
        )
        # NOTE: SessionStart is intentionally NOT wired here — deferred to Phase 9
        # (needs `change resume` no-arg active-change resolution + AGENTS.md work).

    def inject_context(self, change_id: str) -> str:
        """Return the ``change resume`` context dump for ``change_id``.

        Delegates to ``super-harness change resume <change_id>`` and returns its
        stdout. A non-zero exit or empty stdout (e.g. unknown slug) yields ``""``
        rather than raising — context injection is best-effort.
        """
        result = subprocess.run(
            [_CLI_BINARY, "change", "resume", change_id],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.stdout or ""

    def agents_md_subsection(self) -> str:
        """Return the marker-wrapped AGENTS.md subsection for Claude Code.

        Content-only (per spec §3.5 the actual AGENTS.md injection is Phase 9):
        teaches the agent that a PreToolUse gate is enforced and how to recover
        from a block (`super-harness status` + `change resume`).
        """
        return _AGENTS_MD_SUBSECTION

    def on_uninstall(self, workspace: Path) -> None:
        """Best-effort restore of the most recent settings.json backup.

        ``merge_pre_tool_use_hook`` backs the user's file up to
        ``settings.json.super-harness-backup.<unix-ts>`` before each write. On
        uninstall we restore the newest such backup (highest timestamp) to undo
        our hook entry. If no backup exists we leave the file untouched — a
        minimal, documented best-effort suitable for v0.1 (clean per-entry
        removal is a Phase 9+ refinement).
        """
        settings_path = workspace / ".claude" / "settings.json"
        backups = sorted(
            settings_path.parent.glob(f"{settings_path.name}.super-harness-backup.*"),
            key=_backup_sort_key,
        )
        if not backups:
            return
        settings_path.write_text(backups[-1].read_text())


def _backup_sort_key(path: Path) -> int:
    """Sort key extracting the trailing unix-ts from a backup filename.

    Backups are named ``settings.json.super-harness-backup.<ts>``; sorting by the
    integer ts orders them chronologically so the newest restores last. A
    non-integer suffix sorts first (treated as oldest) so a malformed name never
    shadows a real, newer backup.
    """
    suffix = path.name.rsplit(".", 1)[-1]
    try:
        return int(suffix)
    except ValueError:
        return -1
