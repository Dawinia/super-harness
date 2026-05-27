from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import ClassVar

import pytest

from super_harness.gates import (
    Gate,
    GateDecision,
    GateFiresOn,
    GateResult,
    ProposedAction,
)


class _AlwaysAllow(Gate):
    name: ClassVar[str] = "always-allow"
    version: ClassVar[str] = "0.1.0"
    fires_on: ClassVar[GateFiresOn] = "pre_tool_use"

    def decide(self, action, state, events):  # type: ignore[no-untyped-def]
        return GateResult(decision=GateDecision.ALLOW)


def test_gate_subclass_instantiable() -> None:
    g = _AlwaysAllow()
    assert g.name == "always-allow"
    assert g.fires_on == "pre_tool_use"
    result = g.decide(ProposedAction(kind="edit"), None, [])
    assert result.decision is GateDecision.ALLOW


def test_gate_result_defaults() -> None:
    r = GateResult(decision=GateDecision.BLOCK)
    assert r.reason == ""
    assert r.related_events == []
    assert r.blocked_action is None
    assert r.suggested_action is None


def test_proposed_action_defaults() -> None:
    a = ProposedAction(kind="edit")
    assert a.file is None
    assert a.pr_number is None
    assert a.commit_sha is None


def test_gate_is_abstract() -> None:
    with pytest.raises(TypeError):
        Gate()  # type: ignore[abstract]


def test_gate_decision_values() -> None:
    assert GateDecision.ALLOW.value == "allow"
    assert GateDecision.BLOCK.value == "block"
    assert {m.name for m in GateDecision} == {"ALLOW", "BLOCK"}


def test_gate_result_is_frozen() -> None:
    r = GateResult(decision=GateDecision.ALLOW)
    with pytest.raises(FrozenInstanceError):
        r.decision = GateDecision.BLOCK  # type: ignore[misc]


def test_proposed_action_is_frozen() -> None:
    a = ProposedAction(kind="edit")
    with pytest.raises(FrozenInstanceError):
        a.file = "x.py"  # type: ignore[misc]


def test_gate_subclass_must_define_name() -> None:
    with pytest.raises(TypeError, match="name"):

        class _Bad(Gate):
            version: ClassVar[str] = "0.1.0"

            def decide(self, action, state, events):  # type: ignore[no-untyped-def]
                return GateResult(decision=GateDecision.ALLOW)


def test_gate_subclass_must_define_version() -> None:
    with pytest.raises(TypeError, match="version"):

        class _Bad2(Gate):
            name: ClassVar[str] = "bad"
            # version defaults to "0.0.0" — should fail

            def decide(self, action, state, events):  # type: ignore[no-untyped-def]
                return GateResult(decision=GateDecision.ALLOW)
