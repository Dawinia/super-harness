"""Adapter base architecture for super-harness.

Per adapter-architecture §2.2: an AgentAdapter bridges an AI coding agent
runtime (Claude Code / Cursor / Codex / Aider) to super-harness — it installs
hooks for deterministic gate enforcement and injects context for cross-session
continuity (Ralph Loop).

Per adapter-architecture §2.1: a FrameworkAdapter bridges a spec-driven
framework (OpenSpec / Spec Kit / Superpowers / Plain) to super-harness —
observes framework artifacts, emits lifecycle events, and contributes
verification checks.

Public surface:
- AgentAdapter (ABC) — subclass to support a new agent runtime
- FrameworkAdapter (ABC) — subclass to support a new spec framework
- WorkspaceContext — re-exported from super_harness.core.workspace (single source of truth)

v0.1 ships only the ABCs here. Concrete adapters, the adapter registry, and
the `adapter install` CLI come in later tasks.

Concrete AgentAdapter subclasses must declare `capabilities` with the v0.1
canonical 8 keys (adapters do not invent their own; v0.2 adds a reserved
`x_<vendor>_*` prefix for extensions — see adapter-architecture §2.2):
    pre_tool_use_hook    # agent tool-call pre hook (real-time deterministic gate)
    post_tool_use_hook   # agent tool-call post hook (result inspection)
    session_start_hook   # session-start context injection
    session_end_hook     # session-end cleanup hook
    pre_commit_hook      # agent-side git commit hook (distinct from git hook)
    rules_file_injection  # static rules file (.cursorrules / AGENTS.md)
    mcp_server           # MCP protocol server integration
    subprocess_execution  # can run super-harness CLI subprocess

See adapter-architecture spec §2.2 for the full contract.

API stability: **experimental** (v0.1). The adapter interfaces may change in
v0.2 without backwards compatibility. Pin to v0.1 if depending on this API.
"""

from __future__ import annotations

import inspect
from abc import ABC, abstractmethod
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from super_harness.core.events import Event
from super_harness.core.workspace import WorkspaceContext

if TYPE_CHECKING:
    # Type-only import (no runtime coupling). `core.authoring_check` imports only
    # `core`, and this is the normal downward direction (adapters build on core), so
    # the core-is-base contract — which forbids core → {cli,gates,sensors} — is unaffected.
    from super_harness.core.authoring_check import Verdict

__all__ = [
    "AgentAdapter",
    "FrameworkAdapter",
    "WorkspaceContext",
]


class AgentAdapter(ABC):
    """Bridge to an AI coding agent runtime (Claude Code / Cursor / Codex / Aider).

    Adapter installs hooks for deterministic gate enforcement; injects context
    for cross-session continuity (Ralph Loop).
    See adapter-architecture spec §2.2 for the full contract.
    Subclasses must define `name` (non-empty) and `version` (not the default
    "0.0.0"), and fill `capabilities` with the v0.1 canonical 8 keys.
    """

    name: ClassVar[str] = ""
    version: ClassVar[str] = "0.0.0"
    # Platform capability declaration — influences install behaviour + degraded
    # mode docs. Concrete adapters fill the v0.1 canonical 8 keys (see module
    # docstring + adapter-architecture §2.2).
    capabilities: ClassVar[dict[str, bool]] = {}

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if inspect.isabstract(cls):
            return
        if not cls.name:
            raise TypeError(
                f"{cls.__name__} must define a non-empty `name` class attribute"
            )
        if not cls.version or cls.version == "0.0.0":
            raise TypeError(
                f"{cls.__name__} must define `version` (not the default '0.0.0')"
            )

    @abstractmethod
    def detect(self, workspace: Path) -> bool:
        """Detect whether `workspace` uses this agent.

        Typically a feature-file check (e.g. `.claude/` exists = Claude Code).
        """
        ...

    @abstractmethod
    def install_hooks(self, workspace: Path) -> None:
        """Install agent-specific hook implementations in `workspace`.

        e.g. Claude Code adapter modifies `.claude/settings.json` to register a
        PreToolUse hook. Agents without real-time hooks only write a static
        rules file and document degraded mode (CI + git hook fallback).
        """
        ...

    @abstractmethod
    def inject_context(self, change_id: str) -> str:
        """Return an agent-ready context string for session-start injection.

        Typical contents: current state / event sequence / scope / pending
        sensor results — the agent-specific wrapping of `change resume`.
        """
        ...

    @abstractmethod
    def agents_md_subsection(self) -> str:
        """Return agent-specific instructions for the AGENTS.md agent subsection.

        e.g. Claude Code: how to read a PreToolUse gate block and how to resume.
        """
        ...

    def on_uninstall(self, workspace: Path) -> None:  # noqa: B027
        """Clean up agent-specific hook installation (default = no-op).

        Intentionally a non-abstract no-op (per adapter-architecture §2.2):
        adapters opt in to extra cleanup by overriding; not overriding is valid.

        super-harness handles generic cleanup (removing the AGENTS.md subsection
        / verification.yaml injection); adapters only override this for special
        cleanup (e.g. removing a hook entry from `.claude/settings.json`).
        """
        pass

    def local_config_relpath(self) -> str:
        """Workspace-relative path of the per-machine hook config this adapter
        writes (e.g. ``.claude/settings.local.json``). Default ``""`` = none.
        Used only for CLI install messaging."""
        return ""

    def installed_detail(self) -> str:
        """One-line post-install summary for the CLI (e.g. where the gate hook
        landed + any required follow-up). Default is generic."""
        return "agent hooks registered"

    def stop_should_check(self, payload: dict[str, Any]) -> bool:
        """Whether to run the authoring check for this turn-end (Stop) event.

        Default ``True`` (check every turn end). Agents whose Stop payload carries a
        re-entrancy guard override this to skip the continuation turn a prior block
        created, so a nudge never loops. This is the FIRST half of an agent's Stop
        protocol; :meth:`format_stop_feedback` is the second — both live on the adapter
        so the orchestrator (`hook_entry._run_stop`) stays free of agent field names."""
        return True

    def format_stop_feedback(self, verdict: Verdict) -> str:
        """Format a turn-end conformance verdict for this agent's Stop-hook feedback
        channel; return ``""`` to deliver nothing.

        Default = floor-only: agents whose Stop hook cannot feed text back to the model
        do not override this and rely on the CI cold-path floor. Agents that can
        (Claude Code, Codex) override it. Takes the STRUCTURED ``verdict`` so an agent
        can choose channel/fields; use :meth:`_render_advisory` for the shared prose.
        """
        return ""

    @staticmethod
    def _render_advisory(verdict: Verdict) -> str:
        """Shared agent-agnostic advisory prose: the decision id + the check's own
        detail + a decision-doc pointer. No fabricated fix text (design §3b)."""
        lines = [
            "super-harness authoring-time check — a ratified decision's check is "
            "failing for your changes:",
        ]
        for v in verdict.violations:
            lines.append(f"  • {v.decision_id}: {v.detail}")
            lines.append(f"    (rule + counterexample: {v.decision_doc_path})")
        lines.append(
            "Correct it before finishing this turn; the merge gate will otherwise "
            "reject it later. If you believe this is a legitimate exception, stop and "
            "surface it to the human — do not proceed on your own authority."
        )
        return "\n".join(lines)


class FrameworkAdapter(ABC):
    """Bridge to a spec-driven framework (OpenSpec / Spec Kit / Superpowers / Plain).

    Adapter observes framework artifacts -> emits lifecycle events; contributes
    verification checks to .harness/verification.yaml.adapter_provided.
    See adapter-architecture spec §2.1 for the full contract.
    Subclasses must define `name` (non-empty) and `version` (not the default
    "0.0.0").
    """

    name: ClassVar[str] = ""
    version: ClassVar[str] = "0.0.0"
    # When True the dispatcher force-activates this adapter only when all
    # non-fallback adapters' detect() return False (e.g. the "plain" adapter).
    is_fallback: ClassVar[bool] = False

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if inspect.isabstract(cls):
            return
        if not cls.name:
            raise TypeError(
                f"{cls.__name__} must define a non-empty `name` class attribute"
            )
        if not cls.version or cls.version == "0.0.0":
            raise TypeError(
                f"{cls.__name__} must define `version` (not the default '0.0.0')"
            )

    @abstractmethod
    def detect(self, workspace: Path) -> bool:
        """Detect whether `workspace` uses this framework.

        Typically a feature-file check (e.g. `.super-harness/` or `spec/` exists).
        """
        ...

    @abstractmethod
    def observe(self, workspace: Path) -> Iterator[Event]:
        """Observe framework artifacts in `workspace` and yield lifecycle events.

        Called by the daemon to collect events that should be appended to
        events.jsonl. Implementations should yield only new/unseen events.
        """
        ...

    @abstractmethod
    def get_state(self, change_id: str) -> dict[str, Any] | None:
        """Return current framework state for `change_id`, or None if unknown.

        The returned dict is stored as `Event.framework_state` on emitted events.
        """
        ...

    @abstractmethod
    def verification_checks(self) -> list[dict[str, Any]]:
        """Return adapter-provided verification checks for verification.yaml.

        Each dict represents one check entry contributed to
        .harness/verification.yaml.adapter_provided.
        """
        ...

    @abstractmethod
    def agents_md_subsection(self) -> str:
        """Return framework-specific instructions for the AGENTS.md subsection."""
        ...

    def watch_paths(self, workspace: Path) -> list[Path]:
        """Return filesystem paths the daemon should watch for live observe().

        Additive non-abstract default = `[]` (no live watch): adapters opt into
        filesystem watching by overriding. The plain fallback inherits the empty
        default unchanged. Framework adapters that observe on-disk artifacts (e.g.
        OpenSpec watches `openspec/changes/`) override this so the daemon knows
        which directories to subscribe to before calling `observe`.
        """
        return []

    def on_uninstall(self, workspace: Path) -> None:  # noqa: B027
        """Clean up framework-specific artifacts from `workspace` (default = no-op).

        Intentionally a non-abstract no-op (per adapter-architecture §2.1):
        adapters opt in to extra cleanup by overriding; not overriding is valid.
        """
        pass

    def spec_paths(self, workspace: Path, change_id: str) -> dict[str, str]:
        """Resolve this change's spec + plan file paths (HG-01).

        Additive non-abstract default = both empty: a framework with no spec/plan
        concept (e.g. plain) inherits this unchanged. Adapters that have on-disk
        spec/plan artifacts override it.

        MUST be PURE PATH DERIVATION — no I/O beyond joining paths. The verification
        runner calls this to fill the `${SPEC_PATH}` / `${PLAN_PATH}` interpolation
        variables, and it runs inside the daemon (cwd=`/`), so this must NOT call
        `get_state` or otherwise touch the filesystem. Paths are returned whether or
        not the files exist — existence is the check author's concern.

        Returns `{"spec": <path-or-empty>, "plan": <path-or-empty>}`.
        """
        return {"spec": "", "plan": ""}
