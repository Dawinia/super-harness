import pytest

from super_harness.core.state import STATES, ChangeState
from super_harness.gates import GateDecision, ProposedAction
from super_harness.gates.pre_tool_use import PreToolUseGate


def _cs(state: str) -> ChangeState:
    return ChangeState(change_id="c1", current_state=state)

def test_blocks_intent_declared():
    r = PreToolUseGate().decide(
        ProposedAction(kind="edit", file="a.py"), _cs("INTENT_DECLARED"), []
    )
    assert r.decision is GateDecision.BLOCK
    assert "INTENT_DECLARED" in r.reason
    assert r.suggested_action  # non-empty next step

def test_allows_plan_approved():
    r = PreToolUseGate().decide(ProposedAction(kind="edit", file="a.py"), _cs("PLAN_APPROVED"), [])
    assert r.decision is GateDecision.ALLOW

def test_no_active_change_allows():
    r = PreToolUseGate().decide(ProposedAction(kind="edit"), None, [])
    assert r.decision is GateDecision.ALLOW

@pytest.mark.parametrize("state", STATES)
def test_decides_every_state(state):
    PreToolUseGate().decide(ProposedAction(kind="edit"), _cs(state), [])
