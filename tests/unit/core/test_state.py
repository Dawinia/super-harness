from super_harness.core.state import STATES, TERMINAL_STATES, ChangeState


def test_ten_states_in_correct_order():
    assert STATES == (
        "INTENT_DECLARED",
        "AWAITING_PLAN_REVIEW",
        "PLAN_REJECTED",
        "PLAN_APPROVED",
        "IMPLEMENTATION_IN_PROGRESS",
        "AWAITING_CODE_REVIEW",
        "CODE_REVIEW_REJECTED",
        "READY_TO_MERGE",
        "ARCHIVED",
        "ABANDONED",
    )
    assert len(STATES) == 10
    assert "MERGED" not in STATES


def test_terminal_states():
    assert TERMINAL_STATES == frozenset({"ARCHIVED", "ABANDONED"})


def test_change_state_default_construction():
    cs = ChangeState(change_id="c1")
    assert cs.change_id == "c1"
    assert cs.current_state == "INTENT_DECLARED"
    assert cs.framework == "plain"
    assert cs.event_counts == {}
    assert cs.affected_anchors == []
    assert cs.redeclaration_history == []
    assert cs.pr_url is None
