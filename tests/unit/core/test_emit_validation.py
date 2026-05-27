from pathlib import Path

import pytest

from super_harness.core.writer import EmitPreconditionError, EventWriter
from tests.unit.core.test_writer import _make_event


def test_emit_plan_ready_without_intent_declared_rejected(tmp_path: Path):
    w = EventWriter(tmp_path / "events.jsonl")
    with pytest.raises(EmitPreconditionError) as exc:
        w.emit(_make_event("c1", "plan_ready"))
    assert "plan_ready" in str(exc.value)
    assert "INTENT_DECLARED" in str(exc.value) or "intent_declared" in str(exc.value)


def test_emit_implementation_complete_without_verification_rejected(tmp_path: Path):
    w = EventWriter(tmp_path / "events.jsonl")
    # establish valid lifecycle up to IMPLEMENTATION_IN_PROGRESS
    w.emit(_make_event("c1", "intent_declared"))
    w.emit(_make_event("c1", "plan_ready"))
    w.emit(_make_event("c1", "plan_approved"))
    w.emit(_make_event("c1", "implementation_started"))
    # implementation_complete without prior verification_passed: rejected
    with pytest.raises(EmitPreconditionError, match="requires prior"):
        w.emit(_make_event("c1", "implementation_complete"))


def test_emit_valid_path_succeeds(tmp_path: Path):
    w = EventWriter(tmp_path / "events.jsonl")
    for t in [
        "intent_declared",
        "plan_ready",
        "plan_approved",
        "implementation_started",
        "verification_passed",
        "implementation_complete",
    ]:
        w.emit(_make_event("c1", t))
    assert (tmp_path / "events.jsonl").read_text().count("\n") == 6
