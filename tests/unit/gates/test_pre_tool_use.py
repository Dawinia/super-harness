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


# --- Scratch-area allowance (design 2026-07-29) ---


def _state(current: str, change_id: str = "my-change") -> ChangeState:
    return ChangeState(change_id=change_id, current_state=current)


def _decide(state, resolved, **kw):
    return PreToolUseGate(**kw).decide(
        ProposedAction(kind="edit", file=resolved, resolved_path=resolved), state, []
    )


@pytest.mark.parametrize(
    "current",
    [
        "INTENT_DECLARED",
        "AWAITING_PLAN_REVIEW",
        "PLAN_REJECTED",
        "AWAITING_CODE_REVIEW",
        "READY_TO_MERGE",
        "ARCHIVED",
        "ABANDONED",
    ],
)
def test_scratch_area_allowed_in_every_blocking_state(current):
    r = _decide(_state(current), ".harness/scratch/my-change/notes.md")
    assert r.decision is GateDecision.ALLOW


def test_scratch_area_allows_any_extension():
    # It never enters git, so there is no reason to restrict it to .md.
    r = _decide(_state("READY_TO_MERGE"), ".harness/scratch/my-change/probe.py")
    assert r.decision is GateDecision.ALLOW


def test_other_changes_scratch_is_blocked():
    r = _decide(_state("INTENT_DECLARED"), ".harness/scratch/other-change/notes.md")
    assert r.decision is GateDecision.BLOCK


def test_scratch_sibling_prefix_is_not_a_match():
    # `.harness/scratch/my-change-evil/` must NOT satisfy the `my-change` prefix.
    r = _decide(_state("INTENT_DECLARED"), ".harness/scratch/my-change-evil/x.md")
    assert r.decision is GateDecision.BLOCK


def test_scratch_bare_directory_path_is_blocked():
    r = _decide(_state("INTENT_DECLARED"), ".harness/scratch/my-change")
    assert r.decision is GateDecision.BLOCK


def test_gate_disabled_path_is_never_allowed_via_scratch():
    # Post-canonicalization the traversal has already resolved; the gate sees the
    # real target and must block it.
    r = _decide(_state("INTENT_DECLARED"), ".harness/gate-disabled")
    assert r.decision is GateDecision.BLOCK
