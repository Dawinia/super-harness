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


# --- PLAN_REJECTED plan-artifact carve-out (HG-PLAN-AUTHORING) ---


def _st(state: str, **kw: object) -> ChangeState:
    return ChangeState(change_id="c1", current_state=state, **kw)  # type: ignore[arg-type]


def _act(f: str, rp: str | None) -> ProposedAction:
    return ProposedAction(kind="edit", file=f, resolved_path=rp)


def test_carveout_allows_recorded_artifact() -> None:
    st = _st("PLAN_REJECTED", plan_artifacts=["docs/plans/c.md"])
    assert (
        PreToolUseGate().decide(_act("docs/plans/c.md", "docs/plans/c.md"), st, []).decision
        is GateDecision.ALLOW
    )


def test_carveout_blocks_source_even_in_scope() -> None:
    st = _st("PLAN_REJECTED", plan_artifacts=["docs/plans/c.md"])
    assert (
        PreToolUseGate().decide(_act("src/evil.py", "src/evil.py"), st, []).decision
        is GateDecision.BLOCK
    )


def test_carveout_blocks_unrecorded_md() -> None:
    st = _st("PLAN_REJECTED", plan_artifacts=["docs/plans/c.md"])
    assert (
        PreToolUseGate().decide(_act("docs/other.md", "docs/other.md"), st, []).decision
        is GateDecision.BLOCK
    )


def test_carveout_blocks_recorded_non_md_defense_in_depth() -> None:
    # even if a non-.md path somehow reached plan_artifacts, the gate .md guard blocks it
    st = _st("PLAN_REJECTED", plan_artifacts=["src/evil.py"])
    assert (
        PreToolUseGate().decide(_act("src/evil.py", "src/evil.py"), st, []).decision
        is GateDecision.BLOCK
    )


def test_carveout_blocks_when_resolved_none() -> None:
    st = _st("PLAN_REJECTED", plan_artifacts=["docs/plans/c.md"])
    assert (
        PreToolUseGate().decide(_act("/etc/passwd", None), st, []).decision
        is GateDecision.BLOCK
    )


def test_carveout_blocks_when_no_artifacts() -> None:
    st = _st("PLAN_REJECTED", plan_artifacts=[])
    assert (
        PreToolUseGate().decide(_act("docs/plans/c.md", "docs/plans/c.md"), st, []).decision
        is GateDecision.BLOCK
    )


def test_carveout_blocks_when_plan_artifacts_forged_non_list() -> None:
    # forged/corrupt state.yaml: plan_artifacts is a string → must BLOCK, not raise
    st = _st("PLAN_REJECTED")
    st.plan_artifacts = "docs/plans/c.md"  # type: ignore[assignment]
    assert (
        PreToolUseGate().decide(_act("docs/plans/c.md", "docs/plans/c.md"), st, []).decision
        is GateDecision.BLOCK
    )


def test_carveout_allows_uppercase_md_extension() -> None:
    st = _st("PLAN_REJECTED", plan_artifacts=["docs/plans/C.MD"])
    assert (
        PreToolUseGate().decide(_act("docs/plans/C.MD", "docs/plans/C.MD"), st, []).decision
        is GateDecision.ALLOW
    )


def test_carveout_awaiting_never_allows() -> None:
    st = _st("AWAITING_PLAN_REVIEW", plan_artifacts=["docs/plans/c.md"])
    assert (
        PreToolUseGate().decide(_act("docs/plans/c.md", "docs/plans/c.md"), st, []).decision
        is GateDecision.BLOCK
    )
