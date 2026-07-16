"""PreToolUseGate — in-process gate for the 10-state pre-tool-use matrix.

Per sensor-gate-architecture §2.2 + lifecycle-event-model §3.7. This gate
reads the canonical policy from `super_harness.gates.decisions` (the single
source of truth shared with the daemon) and maps each ChangeState to an
allow/block verdict. It does NOT invent policy — it only executes the table.

API stability: **experimental** (v0.1). See `super_harness.gates` for the
Gate contract; this gate may change in v0.2 without backwards compatibility.
"""
from __future__ import annotations

from typing import ClassVar

from super_harness.core.events import Event
from super_harness.core.state import ChangeState
from super_harness.gates import (
    Gate,
    GateDecision,
    GateFiresOn,
    GateResult,
    ProposedAction,
)
from super_harness.gates.decisions import (
    PLAN_ARTIFACT_ALLOW_STATES,
    PRE_TOOL_USE_DECISIONS,
    SUGGESTIONS,
)


class PreToolUseGate(Gate):
    """Allow/block an agent's file edit based on the change's lifecycle state.

    Reads the canonical policy from `gates.decisions`: the 10-state
    `PRE_TOOL_USE_DECISIONS` matrix plus the `PLAN_ARTIFACT_ALLOW_STATES` carve-out
    (the PLAN_REJECTED plan-artifact narrowing). It reads both — it does not fork or
    invent policy. With no active change (state is None) the gate allows. See
    lifecycle-event-model §3.7 for the per-state rationale + the carve-out.
    """

    name: ClassVar[str] = "pre-tool-use"
    version: ClassVar[str] = "0.1.0"
    fires_on: ClassVar[GateFiresOn] = "pre_tool_use"

    def decide(
        self,
        action: ProposedAction,
        state: ChangeState | None,
        events: list[Event],
    ) -> GateResult:
        if state is None:
            return GateResult(decision=GateDecision.ALLOW, reason="no active change")
        # Plan-artifact carve-out (HG-PLAN-AUTHORING), read from the single policy
        # module. In a PLAN_ARTIFACT_ALLOW_STATES state, an edit to one of the
        # change's recorded plan artifacts (a marked `.md`) is ALLOWED. Guards, in
        # order: `.md` (case-insensitive) so no source path qualifies even if forged
        # into the list; `isinstance(list)` so a forged non-list state.yaml yields a
        # clean BLOCK instead of a `TypeError` the hook would fail-open on. Everything
        # else falls through to the table below (BLOCK).
        rp = action.resolved_path
        if (
            state.current_state in PLAN_ARTIFACT_ALLOW_STATES
            and rp
            and rp.lower().endswith(".md")
            and isinstance(state.plan_artifacts, list)
            and rp in state.plan_artifacts
        ):
            return GateResult(
                decision=GateDecision.ALLOW,
                reason=f"{state.current_state}: plan-artifact revision authorized ({rp})",
            )
        decision_str, reason = PRE_TOOL_USE_DECISIONS.get(
            state.current_state, ("block", f"unknown state: {state.current_state}")
        )
        decision = (
            GateDecision.ALLOW if decision_str == "allow" else GateDecision.BLOCK
        )
        blocked = f"{action.kind} {action.file or ''}".strip() or None
        return GateResult(
            decision=decision,
            reason=reason,
            blocked_action=blocked,
            suggested_action=SUGGESTIONS.get(state.current_state),
        )
