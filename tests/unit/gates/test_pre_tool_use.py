import pytest

from super_harness.core.state import ChangeState
from super_harness.gates import GateDecision, ProposedAction
from super_harness.gates.decisions import PRE_TOOL_USE_DECISIONS
from super_harness.gates.pre_tool_use import PreToolUseGate


def _cs(state: str) -> ChangeState:
    return ChangeState(change_id="c1", current_state=state)


def test_blocks_intent_declared() -> None:
    r = PreToolUseGate().decide(
        ProposedAction(kind="edit", file="a.py"), _cs("INTENT_DECLARED"), []
    )
    assert r.decision is GateDecision.BLOCK
    assert "INTENT_DECLARED" in r.reason
    assert r.suggested_action  # non-empty next step


def test_allows_plan_approved() -> None:
    r = PreToolUseGate().decide(
        ProposedAction(kind="edit", file="a.py"), _cs("PLAN_APPROVED"), []
    )
    assert r.decision is GateDecision.ALLOW


def test_no_active_change_allows() -> None:
    r = PreToolUseGate().decide(ProposedAction(kind="edit"), None, [])
    assert r.decision is GateDecision.ALLOW


@pytest.mark.parametrize(
    ("state", "expected"), [(s, d) for s, (d, _) in PRE_TOOL_USE_DECISIONS.items()]
)
def test_decides_every_state(state: str, expected: str) -> None:
    r = PreToolUseGate().decide(ProposedAction(kind="edit"), _cs(state), [])
    assert r.decision.value == expected
