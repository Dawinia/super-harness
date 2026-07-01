"""Claude-Code-hook *family* Stop protocol (shared by Claude Code + Codex).

Codex deliberately clones the Claude Code hook interface (see codex.py docstring),
so both agents' turn-end Stop hooks use the SAME payload guard field
(`stop_hook_active`) and the SAME feedback envelope (`{"decision":"block","reason"}`).
This module is that shared protocol — NOT a universal truth: a third agent with a
different turn-end mechanism must NOT reuse this; it writes its own. Verified against
`codex exec` in private/research/2026-07-01-codex-stop-spike.md (reason is the ONLY
channel that reaches the model; systemMessage / additionalContext break Codex's Stop).

`block_feedback` composes `AgentAdapter._render_advisory` (agnostic Verdict->prose). That
prose deliberately stays on the base class, NOT relocated to `core.authoring_check`
(considered + rejected: it would touch core, and core owns structured verdicts not
presentation prose — see design 2026-07-01-codex-stop-delivery-design.md §4.1).
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from super_harness.adapters import AgentAdapter

if TYPE_CHECKING:
    from super_harness.core.authoring_check import Verdict


def is_continuation(payload: dict[str, Any]) -> bool:
    """True on the continuation turn a prior block created — the re-entrancy guard.
    STRICT: only the literal bool ``True`` counts (a ``"true"`` string does not)."""
    return payload.get("stop_hook_active") is True


def block_feedback(verdict: Verdict) -> str:
    """The family Stop envelope: ``{"decision":"block","reason": advisory}`` when a
    violation is present, ``""`` when clean. reason-ONLY by design (spike §Q4)."""
    if not verdict.violations:
        return ""
    return json.dumps(
        {"decision": "block", "reason": AgentAdapter._render_advisory(verdict)}
    )
