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


def test_traversal_out_of_scratch_resolves_and_blocks(tmp_path):
    # Proves the actual composition (canonical_relpath -> gate), not just that
    # the gate blocks an already-resolved string. A raw `..` traversal out of
    # the scratch tree must canonicalize to its real target BEFORE the gate
    # ever sees it, and that real target (the gate's own kill switch) blocks.
    from super_harness.core.paths import canonical_relpath

    rp = canonical_relpath(tmp_path, ".harness/scratch/my-change/../../gate-disabled")
    assert rp == ".harness/gate-disabled"  # canonicalization did its job
    r = _decide(_state("INTENT_DECLARED"), rp)
    assert r.decision is GateDecision.BLOCK  # and the gate blocks the real target


# --- Forged `change_id` (state.yaml is gitignored + agent-writable) ---


@pytest.mark.parametrize("forged", [None, 0, 42, [], {}, True, "", "..", "../..", "a/../.."])
def test_forged_change_id_never_raises_and_never_widens(forged):
    # A forged non-str/empty/traversal-shaped change_id must never raise (the
    # gate fails OPEN on exceptions) and must never turn into a widened ALLOW
    # for an ordinary source path outside any scratch tree.
    r = _decide(_state("INTENT_DECLARED", change_id=forged), "src/api.py")
    assert r.decision is GateDecision.BLOCK


@pytest.mark.parametrize("forged", [None, 0, 42, [], {}, True, "", "..", "../..", "a/../.."])
def test_forged_change_id_cannot_allow_outside_scratch_tree(forged):
    # Same forged ids, but this time the probe path lives under `.harness/scratch/`
    # for a DIFFERENT, real-looking change id — a forged id must not accidentally
    # match that prefix and widen the allowance to someone else's scratch area.
    r = _decide(_state("INTENT_DECLARED", change_id=forged), ".harness/scratch/other-change/x.md")
    assert r.decision is GateDecision.BLOCK


# --- Plan-path allowance (design 2026-07-29) ---

PATTERNS = ["docs/plans/*{slug}*.md"]


def test_plan_path_allowed_in_intent_declared():
    r = _decide(
        _state("INTENT_DECLARED"),
        "docs/plans/2026-07-29-my-change-design.md",
        plan_path_patterns=PATTERNS,
    )
    assert r.decision is GateDecision.ALLOW


def test_plan_path_not_allowed_in_awaiting_plan_review():
    # D1 + design non-goal: the reviewer's frozen target must not move.
    r = _decide(
        _state("AWAITING_PLAN_REVIEW"),
        "docs/plans/2026-07-29-my-change-design.md",
        plan_path_patterns=PATTERNS,
    )
    assert r.decision is GateDecision.BLOCK


def test_plan_path_not_allowed_in_plan_rejected():
    # D1: PLAN_REJECTED keeps using the recorded plan_artifacts list only.
    r = _decide(
        _state("PLAN_REJECTED"),
        "docs/plans/2026-07-29-my-change-design.md",
        plan_path_patterns=PATTERNS,
    )
    assert r.decision is GateDecision.BLOCK


def test_plan_path_for_a_different_slug_is_blocked():
    r = _decide(
        _state("INTENT_DECLARED"),
        "docs/plans/2026-07-29-other-change-design.md",
        plan_path_patterns=PATTERNS,
    )
    assert r.decision is GateDecision.BLOCK


def test_non_md_resolved_path_is_blocked_even_if_pattern_matches():
    # Symlink laundering: `docs/plans/x-my-change.md` -> `src/evil.py` canonicalizes
    # to the .py, which must fail the post-resolution suffix check.
    r = _decide(
        _state("INTENT_DECLARED"), "src/evil.py", plan_path_patterns=PATTERNS
    )
    assert r.decision is GateDecision.BLOCK


def test_no_patterns_configured_blocks_as_before():
    r = _decide(
        _state("INTENT_DECLARED"),
        "docs/plans/2026-07-29-my-change-design.md",
        plan_path_patterns=[],
    )
    assert r.decision is GateDecision.BLOCK


def test_forged_non_list_patterns_block_cleanly():
    # Defence in depth: a non-list must not raise (the hook would fail-open).
    r = _decide(
        _state("INTENT_DECLARED"),
        "docs/plans/2026-07-29-my-change-design.md",
        plan_path_patterns="docs/plans/*{slug}*.md",  # type: ignore[arg-type]
    )
    assert r.decision is GateDecision.BLOCK


def test_source_file_never_allowed_in_intent_declared():
    r = _decide(
        _state("INTENT_DECLARED"), "src/api.py", plan_path_patterns=PATTERNS
    )
    assert r.decision is GateDecision.BLOCK


def test_forged_glob_metachars_in_change_id_do_not_widen_the_pattern():
    """`change_id` comes from the gitignored state.yaml, which the agent can write
    in any ALLOW state. A `*` in it must NOT turn `docs/plans/*{slug}*.md` into a
    match for every plan document."""
    r = _decide(
        _state("INTENT_DECLARED", change_id="*"),
        "docs/plans/2026-07-29-somebody-elses-plan.md",
        plan_path_patterns=PATTERNS,
    )
    assert r.decision is GateDecision.BLOCK


def test_forged_bracket_class_in_change_id_is_literal():
    r = _decide(
        _state("INTENT_DECLARED", change_id="[a-z]"),
        "docs/plans/x-a-y.md",
        plan_path_patterns=PATTERNS,
    )
    assert r.decision is GateDecision.BLOCK


def test_literal_metachar_slug_still_matches_its_own_document():
    """Escaping must not break the legitimate case: a change_id containing a
    metachar still matches the file literally named after it."""
    r = _decide(
        _state("INTENT_DECLARED", change_id="odd*name"),
        "docs/plans/2026-07-29-odd*name-design.md",
        plan_path_patterns=PATTERNS,
    )
    assert r.decision is GateDecision.ALLOW
