import pytest

from super_harness.core.transitions import INVALID, compute_target_state


@pytest.mark.parametrize(
    "current,event_type,expected",
    [
        # === Initial state ===
        (None, "intent_declared", "INTENT_DECLARED"),
        # === Happy path ===
        ("INTENT_DECLARED", "plan_ready", "AWAITING_PLAN_REVIEW"),
        ("AWAITING_PLAN_REVIEW", "plan_approved", "PLAN_APPROVED"),
        ("AWAITING_PLAN_REVIEW", "plan_rejected", "PLAN_REJECTED"),
        ("PLAN_REJECTED", "plan_ready", "AWAITING_PLAN_REVIEW"),  # revise
        ("PLAN_APPROVED", "implementation_started", "IMPLEMENTATION_IN_PROGRESS"),
        ("IMPLEMENTATION_IN_PROGRESS", "implementation_complete", "AWAITING_CODE_REVIEW"),
        ("AWAITING_CODE_REVIEW", "code_review_passed", "READY_TO_MERGE"),
        ("AWAITING_CODE_REVIEW", "code_review_failed", "CODE_REVIEW_REJECTED"),
        # === CODE_REVIEW_REJECTED fix-loop (C1 + C2 fixes) ===
        # Per §3.4 implementation_complete is single-fire; user re-submits via
        # code-reviewer re-run which emits new code_review_passed/failed (§3.7 line 438).
        ("CODE_REVIEW_REJECTED", "code_review_passed", "READY_TO_MERGE"),
        ("CODE_REVIEW_REJECTED", "code_review_failed", "CODE_REVIEW_REJECTED"),
        ("CODE_REVIEW_REJECTED", "implementation_complete", INVALID),  # NOT a re-submit path
        ("READY_TO_MERGE", "merged", "MERGED"),
        ("MERGED", "l1_update_completed", "ARCHIVED"),
        # === Withdraw ===
        ("AWAITING_CODE_REVIEW", "implementation_withdrawn", "READY_TO_MERGE"),
        ("READY_TO_MERGE", "implementation_withdrawn", "READY_TO_MERGE"),
        # === implementation_restarted / implementation_invalidated as universal
        # events (I1 fix) — `* → PLAN_APPROVED` / `* → IMPLEMENTATION_IN_PROGRESS`
        # per §3.6 lines 373-374 (any non-terminal active state) ===
        ("IMPLEMENTATION_IN_PROGRESS", "implementation_restarted", "PLAN_APPROVED"),
        ("IMPLEMENTATION_IN_PROGRESS", "implementation_invalidated", "IMPLEMENTATION_IN_PROGRESS"),
        ("PLAN_APPROVED", "implementation_restarted", "PLAN_APPROVED"),
        ("AWAITING_CODE_REVIEW", "implementation_invalidated", "IMPLEMENTATION_IN_PROGRESS"),
        ("READY_TO_MERGE", "implementation_restarted", "PLAN_APPROVED"),
        ("CODE_REVIEW_REJECTED", "implementation_restarted", "PLAN_APPROVED"),
        # === Universal re-declarations ===
        ("PLAN_APPROVED", "intent_redeclared", "INTENT_DECLARED"),
        ("IMPLEMENTATION_IN_PROGRESS", "plan_redeclared", "INTENT_DECLARED"),
        ("INTENT_DECLARED", "intent_redeclared", "INTENT_DECLARED"),
        # === Abandon (any active state) ===
        ("PLAN_APPROVED", "intent_abandoned", "ABANDONED"),
        ("AWAITING_CODE_REVIEW", "intent_abandoned", "ABANDONED"),
        ("INTENT_DECLARED", "intent_abandoned", "ABANDONED"),
        # === Informational (no state change) ===
        ("IMPLEMENTATION_IN_PROGRESS", "verification_passed", "IMPLEMENTATION_IN_PROGRESS"),
        ("IMPLEMENTATION_IN_PROGRESS", "verification_failed", "IMPLEMENTATION_IN_PROGRESS"),
        ("IMPLEMENTATION_IN_PROGRESS", "scope_drift_detected", "IMPLEMENTATION_IN_PROGRESS"),
        ("READY_TO_MERGE", "pr_opened", "READY_TO_MERGE"),
        ("MERGED", "merged_reverted", "MERGED"),
        ("IMPLEMENTATION_IN_PROGRESS", "sensor_timeout_exceeded", "IMPLEMENTATION_IN_PROGRESS"),
        ("AWAITING_PLAN_REVIEW", "sensor_crashed", "AWAITING_PLAN_REVIEW"),
        # === intent_declared re-emit on active state = description update (no transition) ===
        ("INTENT_DECLARED", "intent_declared", "INTENT_DECLARED"),
        ("PLAN_APPROVED", "intent_declared", "PLAN_APPROVED"),
        # === Illegal transitions ===
        ("INTENT_DECLARED", "implementation_complete", INVALID),
        ("INTENT_DECLARED", "merged", INVALID),
        ("MERGED", "plan_ready", INVALID),
        ("ARCHIVED", "intent_declared", INVALID),
        ("ARCHIVED", "plan_ready", INVALID),
        ("ABANDONED", "intent_declared", INVALID),
        ("ABANDONED", "plan_ready", INVALID),
        # Terminal early-return uses TERMINAL_STATES — even universal events blocked (M2 fix)
        ("ARCHIVED", "implementation_restarted", INVALID),
        ("ABANDONED", "implementation_invalidated", INVALID),
        ("ARCHIVED", "intent_redeclared", INVALID),
        ("ABANDONED", "intent_abandoned", INVALID),
        (None, "plan_ready", INVALID),  # first event must be intent_declared
        (None, "implementation_complete", INVALID),
        ("PLAN_APPROVED", "code_review_passed", INVALID),  # skip review
    ],
)
def test_transition(current: str | None, event_type: str, expected: str) -> None:
    assert compute_target_state(current, event_type) == expected


def test_invalid_is_distinct_string() -> None:
    """INVALID sentinel must not collide with any real state name."""
    from super_harness.core.state import STATES
    assert INVALID not in STATES
