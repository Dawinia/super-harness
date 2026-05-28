"""SSOT structural invariants for the pre-tool-use gate matrix.

These guard `gates.decisions` against future edits drifting from the
lifecycle-event-model §3.7 contract. They do NOT re-assert the spec text
(that lives in the module's verbatim docstring); they enforce the structural
guarantees the rest of the code relies on.
"""

from __future__ import annotations

from super_harness.core.state import STATES
from super_harness.gates.decisions import PRE_TOOL_USE_DECISIONS, SUGGESTIONS


def test_matrix_covers_all_states() -> None:
    assert set(PRE_TOOL_USE_DECISIONS) == set(STATES)


def test_decisions_are_allow_or_block() -> None:
    assert all(d in {"allow", "block"} for d, _ in PRE_TOOL_USE_DECISIONS.values())


def test_suggestions_cover_exactly_blocking_states() -> None:
    blocking = {
        state for state, (d, _) in PRE_TOOL_USE_DECISIONS.items() if d == "block"
    }
    assert set(SUGGESTIONS) == blocking
