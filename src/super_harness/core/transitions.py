"""Lifecycle state transition table per lifecycle-event-model §3.7 Reachability.

compute_target_state(current_state, event_type) returns:
- A target state string from STATES (legal transition)
- `INVALID` sentinel (the literal string "__INVALID__") for illegal transitions

This table is consumed by:
- Task 1.5 emit_validation (emit-time strict rejection)
- Task 1.6 reducer (reducer-time tolerant warn-skip per §3.8.1 layered validation)
- Phase 4 daemon `gate.pre_tool_use` (state-based ALLOW/BLOCK)
"""
from typing import Literal

from super_harness.core.state import TERMINAL_STATES

INVALID: Literal["__INVALID__"] = "__INVALID__"

# Per spec §3.6: events that never change state (informational sensor signals + system audit)
_INFORMATIONAL: frozenset[str] = frozenset({
    "verification_passed", "verification_failed",
    "scope_drift_detected", "merged_reverted", "pr_opened",
    "sensor_timeout_exceeded", "sensor_crashed",
    "gate_bypassed", "gate_bypass_disclosed",
})

# Per spec §3.7 Reachability table — explicit (current_state, event_type) -> target.
# Order: happy path → reject/restart loops → terminal.
# @decision:d-fixed-transition-matrix
_TRANSITIONS: dict[tuple[str, str], str] = {
    # === Happy path ===
    ("INTENT_DECLARED", "plan_ready"): "AWAITING_PLAN_REVIEW",
    ("AWAITING_PLAN_REVIEW", "plan_approved"): "PLAN_APPROVED",
    ("AWAITING_PLAN_REVIEW", "plan_rejected"): "PLAN_REJECTED",
    ("PLAN_REJECTED", "plan_ready"): "AWAITING_PLAN_REVIEW",  # revise + resubmit
    ("PLAN_APPROVED", "implementation_started"): "IMPLEMENTATION_IN_PROGRESS",
    ("IMPLEMENTATION_IN_PROGRESS", "implementation_complete"): "AWAITING_CODE_REVIEW",
    ("AWAITING_CODE_REVIEW", "code_review_passed"): "READY_TO_MERGE",
    ("AWAITING_CODE_REVIEW", "code_review_failed"): "CODE_REVIEW_REJECTED",
    # Per §3.4 implementation_complete is single-fire; §3.7 line 438: user re-submits
    # by re-running code-reviewer which emits new code_review_passed/failed.
    ("CODE_REVIEW_REJECTED", "code_review_passed"): "READY_TO_MERGE",
    ("CODE_REVIEW_REJECTED", "code_review_failed"): "CODE_REVIEW_REJECTED",
    ("READY_TO_MERGE", "merged"): "ARCHIVED",
    # === Withdraw paths (§3.6) ===
    # NOTE: implementation_restarted / implementation_invalidated are universal
    # (`* → PLAN_APPROVED` / `* → IMPLEMENTATION_IN_PROGRESS` per §3.6 lines 373-374)
    # and handled in compute_target_state below, not in this per-state table.
    ("AWAITING_CODE_REVIEW", "implementation_withdrawn"): "READY_TO_MERGE",
    ("READY_TO_MERGE", "implementation_withdrawn"): "READY_TO_MERGE",
}


def compute_target_state(current: str | None, event_type: str) -> str:
    """Compute target state from current state + incoming event.

    Args:
        current: the current state (one of STATES) or None for "first event".
        event_type: the event type (CORE or EXTENSION).

    Returns:
        A state string from STATES on legal transition, or INVALID sentinel.

    Semantics (per spec §3.7 + §3.6):
    - intent_declared: always legal; from None → INTENT_DECLARED; from existing
      state on the same change → keep current (re-emit = description update,
      not state reset). EXCEPT from ARCHIVED/ABANDONED terminal states, where
      it's INVALID (terminal states do not accept new intent_declared).
    - intent_redeclared / plan_redeclared: legal from any non-terminal active
      state → resets to INTENT_DECLARED.
    - intent_abandoned: legal from any active state → ABANDONED.
    - implementation_restarted / implementation_invalidated: per §3.6 lines
      373-374, legal from any non-terminal active state → PLAN_APPROVED /
      IMPLEMENTATION_IN_PROGRESS respectively.
    - Informational events (verification_passed/failed, scope_drift_detected,
      pr_opened, merged_reverted, sensor_*): never change state.
    - Explicit transitions: looked up in the table.
    - Anything else: INVALID.
    """
    # Initial state
    if event_type == "intent_declared":
        if current is None:
            return "INTENT_DECLARED"
        if current in ("ARCHIVED", "ABANDONED"):
            # Terminal states: re-declaration via intent_redeclared/plan_redeclared,
            # not bare intent_declared (which is treated as "update description" elsewhere).
            return INVALID
        return current  # re-emit on active state = description update, no transition

    if current is None:
        # Any non-intent_declared event with no prior state = illegal
        return INVALID

    # Terminal states block all further transitions (only intent_declared on terminal
    # was caught above and rejected). Even universal events (restart/invalidate/
    # redeclared/abandoned) are blocked once a change is ARCHIVED or ABANDONED.
    if current in TERMINAL_STATES:
        return INVALID

    # Informational events: state unchanged
    if event_type in _INFORMATIONAL:
        return current

    # Universal re-declarations (any active state -> reset)
    if event_type in ("intent_redeclared", "plan_redeclared"):
        return "INTENT_DECLARED"

    if event_type == "intent_abandoned":
        return "ABANDONED"

    # Universal restart / invalidate (§3.6 lines 373-374: `* → ...`)
    if event_type == "implementation_restarted":
        return "PLAN_APPROVED"
    if event_type == "implementation_invalidated":
        return "IMPLEMENTATION_IN_PROGRESS"

    # Explicit transition table
    if (current, event_type) in _TRANSITIONS:
        return _TRANSITIONS[(current, event_type)]

    return INVALID
