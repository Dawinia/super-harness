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
- WorkspaceContext — re-exported from super_harness.sensors (single source of truth)

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
from typing import Any, ClassVar

from super_harness.core.events import Event
from super_harness.sensors import WorkspaceContext

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
