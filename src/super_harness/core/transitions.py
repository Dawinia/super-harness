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

INVALID: Literal["__INVALID__"] = "__INVALID__"

# Per spec §3.6: events that never change state (informational sensor signals + system audit)
_INFORMATIONAL: frozenset[str] = frozenset({
    "verification_passed", "verification_failed",
    "scope_drift_detected", "merged_reverted", "pr_opened",
    "l1_update_failed", "sensor_timeout_exceeded", "sensor_crashed",
})

# Per spec §3.7 Reachability table — explicit (current_state, event_type) -> target.
# Order: happy path → reject/restart loops → terminal.
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
    ("CODE_REVIEW_REJECTED", "implementation_complete"): "AWAITING_CODE_REVIEW",  # re-submit
    ("READY_TO_MERGE", "merged"): "MERGED",
    ("MERGED", "l1_update_completed"): "ARCHIVED",
    # === Restart / withdraw paths (§3.6) ===
    ("IMPLEMENTATION_IN_PROGRESS", "implementation_restarted"): "PLAN_APPROVED",
    ("IMPLEMENTATION_IN_PROGRESS", "implementation_invalidated"): "IMPLEMENTATION_IN_PROGRESS",
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
    - Informational events (verification_passed/failed, scope_drift_detected,
      pr_opened, merged_reverted, l1_update_failed, sensor_*): never change state.
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

    # ARCHIVED is a true terminal (only intent_declared was caught above)
    if current == "ARCHIVED":
        return INVALID

    # ABANDONED is terminal too (no events change state)
    if current == "ABANDONED":
        return INVALID

    # Informational events: state unchanged
    if event_type in _INFORMATIONAL:
        return current

    # Universal re-declarations (any active state -> reset)
    if event_type in ("intent_redeclared", "plan_redeclared"):
        return "INTENT_DECLARED"

    if event_type == "intent_abandoned":
        return "ABANDONED"

    # Explicit transition table
    if (current, event_type) in _TRANSITIONS:
        return _TRANSITIONS[(current, event_type)]

    return INVALID
