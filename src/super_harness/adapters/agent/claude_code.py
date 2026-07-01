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
from typing import TYPE_CHECKING, ClassVar

from super_harness.adapters import AgentAdapter

if TYPE_CHECKING:
    from super_harness.core.authoring_check import Verdict
from super_harness.adapters.agent._settings_merge import (
    merge_pre_tool_use_hook,
    merge_session_start_hook,
    merge_stop_hook,
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
- **If a tool call is blocked by the gate:** stop, and surface the block plus the
  next valid step (`super-harness status`) to the human. Do **not** try to disable
  or work around the gate yourself — overriding it is a **human-only** decision, and
  any bypass is recorded and disclosed at the merge gate. Whether to override is the
  human's call.

#### Review protocol

super-harness does NOT review for you — it enforces (via the gate) that a review
verdict is recorded before the lifecycle proceeds, and YOU produce the verdict.
When `super-harness status <change>` reports a review state, it also prints the
configured **strategy** for that reviewer (`subagent` / `human` / `hybrid`):

- **`subagent`** (default) — dispatch a genuinely independent reviewer **subagent**
  (your `Task` tool) to run the checklist, then record the verdict.
- **`human`** — do NOT self-approve. A human reviews and records the verdict; leave
  the change in its review state for them.
- **`hybrid`** — run the subagent first; escalate to a human on a fail (or a Large
  tier change) before recording.

Checklists & verdict verbs per review state:

- **`AWAITING_PLAN_REVIEW`** (plan-reviewer) — check spec coverage / design / scope /
  declared anchors. Record with `super-harness review approve <change> --reviewer
  plan-reviewer` or `super-harness review reject <change> --reviewer plan-reviewer
  --reason "<why>"`. Approve → `PLAN_APPROVED` (gate then allows edits); reject →
  `PLAN_REJECTED` for a revised plan.
- **`AWAITING_CODE_REVIEW`** (code-reviewer) — a code-review approval now REQUIRES a
  structured verdict; a bare `super-harness review approve <change> --reviewer
  code-reviewer` is rejected. The flow:
  1. Commit the in-scope files first — the review digest is taken over the committed
     HEAD diff, so an uncommitted in-scope tree is refused.
  2. `super-harness review prepare <change> --reviewer code-reviewer` — assembles the
     bundle (in-scope diff ∩ scope, out-of-scope drift, spec/plan paths, checklist,
     committed-HEAD digest) to `.harness/pending-reviews/<change>/code-reviewer.bundle.json`.
  3. Hand that bundle to a genuinely independent reviewer **subagent** to run the
     checklist and produce a verdict file (every checklist item gets a status;
     findings required when any item fails; verdict carries the bundle's digest).
  4. `super-harness review approve <change> --reviewer code-reviewer --verdict-file
     <path>` — the verdict is inlined into the emitted event. The approval is refused
     if the verdict is missing/incomplete (a checklist item uncovered) or stale (its
     digest no longer matches the current in-scope committed diff). Approve →
     `READY_TO_MERGE`. (`review reject ... [--verdict-file <path>]` records a fail.)
     If the approval comes out of a REJECTED review, the verdict's `prior_findings` must
     dispose EVERY open finding from the prior `code_review_failed` verdicts
     (`disposition: resolved | wontfix`; `wontfix` needs a `note`) or the approve is refused.
  - plan-reviewer is UNCHANGED this slice: its approve/reject take an optional
    `--verdict-file` (inlined when present) but never require one.
- `super-harness review skip <change> --reviewer <name>` PASSes a stuck reviewer, but for
  `code-reviewer` a BARE skip is a MERGE-GATE BLOCKER (`attest verify` fails). To merge with
  a skip you must record a deliberate, disclosed override:
  `review skip <change> --reviewer code-reviewer --override --reason "<why>"`.

When you do run a subagent, run a genuinely independent one — don't self-rubber-stamp.
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
    }

    def detect(self, workspace: Path) -> bool:
        """A workspace uses Claude Code iff it has a ``.claude/`` directory."""
        return (workspace / ".claude").is_dir()

    def install_hooks(self, workspace: Path) -> None:
        """Register the super-harness PreToolUse + SessionStart hooks.

        Resolves BOTH binaries to absolute paths UP FRONT (so a missing binary
        aborts before any write), then merges three entries into
        ``.claude/settings.local.json`` (no clobber, idempotent — see
        ``merge_pre_tool_use_hook`` / ``merge_session_start_hook`` /
        ``merge_stop_hook``). The hook
        ``command`` pins a machine-specific absolute path, so it belongs in the
        per-machine, conventionally-gitignored ``settings.local.json`` — never
        the committed shared ``settings.json``:

        - **PreToolUse**: ``command`` = ``<abs super-harness-hook> --agent
          claude-code`` (deterministic gate enforcement).
        - **SessionStart**: ``command`` = ``<abs super-harness> change resume``
          (no slug → active change); Claude Code injects its stdout as context.

        Per spec §3.5 this writes NO ``.sh`` script.

        Registering TWO hooks widens the partial-write window, so the settings
        file is snapshotted ONCE before both merges; if either merge raises, the
        snapshot is restored (original bytes rewritten, or the file deleted if it
        was absent) and the error re-raised (spec §3.5 step 3 rollback / OI-9).
        The per-merge backups preserve the *user's* prior content; this snapshot
        is the install *transaction* boundary, a distinct concern.

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

        settings_path = workspace / ".claude" / "settings.local.json"
        pre_tool_use_command = f"{resolved_hook} --agent claude-code"
        # No-arg `change resume` → resume the active change at session start.
        session_start_command = f"{resolved_cli} change resume"
        # Turn-end authoring-time conformance advisory (non-blocking, loop-safe).
        stop_command = f"{resolved_hook} --agent claude-code --event stop"

        # Snapshot the install transaction boundary: capture the file's exact
        # pre-install content, or that it was absent. Restored on ANY failure.
        snapshot: str | None = settings_path.read_text() if settings_path.exists() else None
        try:
            merge_pre_tool_use_hook(settings_path, command=pre_tool_use_command)
            merge_session_start_hook(settings_path, command=session_start_command)
            merge_stop_hook(settings_path, command=stop_command)
        except BaseException:
            self._restore_snapshot(settings_path, snapshot)
            raise

    @staticmethod
    def _restore_snapshot(settings_path: Path, snapshot: str | None) -> None:
        """Restore ``settings_path`` to its pre-install state (snapshot rollback).

        ``snapshot is None`` means the file did not exist pre-install → delete
        whatever a partial merge wrote. Otherwise rewrite the original bytes.
        """
        if snapshot is None:
            settings_path.unlink(missing_ok=True)
        else:
            settings_path.write_text(snapshot)

    def format_stop_feedback(self, verdict: Verdict) -> str:
        """Block the stop and feed the advisory back via Claude Code's Stop-hook JSON
        protocol: ``{"decision":"block","reason": ...}`` (the reason reaches the model
        on its next turn; the edit itself is never undone). Returns ``""`` when clean,
        so the hook allows the stop. Loop-safety (`stop_hook_active`) is enforced by the
        hook entry, not here."""
        import json

        if not verdict.violations:
            return ""
        return json.dumps({"decision": "block", "reason": self._render_advisory(verdict)})

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
        """Best-effort restore of the EARLIEST settings.local.json backup (pristine).

        Targets ``.claude/settings.local.json`` — the per-machine, gitignored
        file ``install_hooks`` wrote to, never the committed shared
        ``settings.json``. Each merge backs the file up to
        ``settings.local.json.super-harness-backup.<time_ns>`` before its write
        (the glob follows ``settings_path.name``). ``install_hooks`` runs TWO
        merges, so a
        single install on a pre-existing file writes TWO backups: the FIRST
        (lowest ts) captures the truly pristine file; the SECOND captures
        pristine + our PreToolUse entry. To undo BOTH of our hooks we must
        restore the EARLIEST backup — restoring the newest would leave our
        PreToolUse entry behind. (Idempotent re-installs write no backup, and a
        binary relocation only adds *newer* backups, so the earliest backup
        stays pristine across re-installs.)

        If no backup exists we leave the file untouched — a minimal, documented
        best-effort suitable for v0.1 (clean per-entry removal is a Phase 9+
        refinement).
        """
        settings_path = workspace / ".claude" / "settings.local.json"
        backups = sorted(
            settings_path.parent.glob(f"{settings_path.name}.super-harness-backup.*"),
            key=_backup_sort_key,
        )
        if not backups:
            return
        settings_path.write_text(backups[0].read_text())


def _backup_sort_key(path: Path) -> int:
    """Sort key extracting the trailing unix-ts from a backup filename.

    Backups are named ``settings.local.json.super-harness-backup.<ts>``; sorting by the
    integer ts orders them chronologically so the newest restores last. A
    non-integer suffix sorts first (treated as oldest) so a malformed name never
    shadows a real, newer backup.
    """
    suffix = path.name.rsplit(".", 1)[-1]
    try:
        return int(suffix)
    except ValueError:
        return -1
