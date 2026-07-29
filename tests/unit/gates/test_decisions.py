"""SSOT structural invariants for the pre-tool-use gate matrix.

These guard `gates.decisions` against future edits drifting from the
lifecycle-event-model §3.7 contract. They do NOT re-assert the spec text
(that lives in the module's verbatim docstring); they enforce the structural
guarantees the rest of the code relies on.
"""

from __future__ import annotations

from super_harness.core.state import STATES
from super_harness.gates.decisions import (
    PLAN_ARTIFACT_ALLOW_STATES,
    PLAN_PATH_ALLOW_STATES,
    PRE_TOOL_USE_DECISIONS,
    SCRATCH_ROOT,
    SUGGESTIONS,
)


def test_matrix_covers_all_states() -> None:
    assert set(PRE_TOOL_USE_DECISIONS) == set(STATES)


def test_decisions_are_allow_or_block() -> None:
    assert all(d in {"allow", "block"} for d, _ in PRE_TOOL_USE_DECISIONS.values())


def test_suggestions_cover_exactly_blocking_states() -> None:
    blocking = {
        state for state, (d, _) in PRE_TOOL_USE_DECISIONS.items() if d == "block"
    }
    assert set(SUGGESTIONS) == blocking


def test_plan_path_allow_states_is_intent_declared_only() -> None:
    # D1: PLAN_REJECTED keeps the plan_artifacts mechanism; the two never overlap.
    assert PLAN_PATH_ALLOW_STATES == frozenset({"INTENT_DECLARED"})


def test_scratch_root_is_under_harness_and_posix() -> None:
    assert SCRATCH_ROOT == ".harness/scratch"
    assert "\\" not in SCRATCH_ROOT


def test_allow_state_sets_are_disjoint() -> None:
    # What makes PreToolUseGate's branch ordering between the two carve-outs
    # irrelevant: they never fire for the same state. Enforced, not incidental —
    # precedent: tests/unit/core/test_events.py asserts
    # CORE_EVENT_TYPES.isdisjoint(EXTENSION_EVENT_TYPES).
    assert PLAN_PATH_ALLOW_STATES.isdisjoint(PLAN_ARTIFACT_ALLOW_STATES)


def test_intent_declared_suggestion_names_the_authoring_space() -> None:
    # The old suggestion ("draft a plan, then mark it ready, then retry the
    # edit") told the agent to do the exact thing the gate itself blocks —
    # that loop is what pushed agents to the shell. The suggestion must name
    # the actual in-gate authoring space: the plan-paths.yaml-configured plan
    # document, plus the always-writable scratch area for working notes.
    s = SUGGESTIONS["INTENT_DECLARED"]
    assert "plan-paths.yaml" in s or "plan document" in s
    assert "scratch" in s
