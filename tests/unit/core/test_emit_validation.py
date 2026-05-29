from pathlib import Path

import pytest

from super_harness.core.emit_validation import (
    OrderingViolation,
    find_ordering_violations,
)
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


# --------------------------------------------------------------------------- #
# find_ordering_violations — whole-stream validator
# --------------------------------------------------------------------------- #


def _seed_raw(tmp_path: Path, change_id: str, types: list[str]) -> Path:
    """Append a stream bypassing emit-time validation (so illegal seqs land on disk)."""
    f = tmp_path / "events.jsonl"
    w = EventWriter(f)
    for t in types:
        w.emit(_make_event(change_id, t), skip_validation=True)
    return f


def test_find_ordering_violations_clean_stream_is_empty(tmp_path: Path):
    f = _seed_raw(
        tmp_path,
        "c1",
        [
            "intent_declared",
            "plan_ready",
            "plan_approved",
            "implementation_started",
            "verification_passed",
            "implementation_complete",
        ],
    )
    assert find_ordering_violations(f, "c1") == []


def test_find_ordering_violations_missing_file_is_empty(tmp_path: Path):
    assert find_ordering_violations(tmp_path / "nope.jsonl", "c1") == []


def test_find_ordering_violations_plan_ready_without_intent(tmp_path: Path):
    # plan_ready as a FIRST event (no prior intent_declared) is illegal.
    f = _seed_raw(tmp_path, "c1", ["plan_ready"])
    violations = find_ordering_violations(f, "c1")
    assert len(violations) == 1
    v = violations[0]
    assert isinstance(v, OrderingViolation)
    assert v.event_type == "plan_ready"
    assert v.from_state is None
    # The offending event_id is captured for diagnostics.
    assert v.event_id
    assert "plan_ready" in v.reason


def test_find_ordering_violations_impl_complete_without_verification(tmp_path: Path):
    # implementation_complete needs a prior verification_passed (hard prereq).
    f = _seed_raw(
        tmp_path,
        "c1",
        [
            "intent_declared",
            "plan_ready",
            "plan_approved",
            "implementation_started",
            "implementation_complete",  # missing verification_passed before it
        ],
    )
    violations = find_ordering_violations(f, "c1")
    # One hard-prereq violation for implementation_complete.
    prereq = [v for v in violations if v.event_type == "implementation_complete"]
    assert len(prereq) == 1
    assert "verification_passed" in prereq[0].reason


def test_find_ordering_violations_preserves_state_after_bad_event(tmp_path: Path):
    # An illegal `merged` from INTENT_DECLARED must NOT advance state — the
    # subsequent legal `plan_ready` should still validate cleanly.
    f = _seed_raw(
        tmp_path,
        "c1",
        ["intent_declared", "merged", "plan_ready"],
    )
    violations = find_ordering_violations(f, "c1")
    # Exactly one violation (the illegal `merged`); plan_ready is fine because
    # state was preserved at INTENT_DECLARED across the bad event.
    assert [v.event_type for v in violations] == ["merged"]
    assert violations[0].from_state == "INTENT_DECLARED"


def test_find_ordering_violations_tolerates_malformed_line(tmp_path: Path):
    f = _seed_raw(tmp_path, "c1", ["intent_declared", "plan_ready"])
    # Inject a truncated/garbage line — must be skipped, not crash.
    with open(f, "a") as fp:
        fp.write('{"event_id":"ev_bad","type":"plan_re')  # no newline, truncated
    assert find_ordering_violations(f, "c1") == []


def test_find_ordering_violations_filters_by_change_id(tmp_path: Path):
    f = tmp_path / "events.jsonl"
    w = EventWriter(f)
    # c1 is clean; c2 has an illegal first event. Only c2 should report.
    w.emit(_make_event("c1", "intent_declared"), skip_validation=True)
    w.emit(_make_event("c2", "plan_ready"), skip_validation=True)
    assert find_ordering_violations(f, "c1") == []
    c2 = find_ordering_violations(f, "c2")
    assert [v.event_type for v in c2] == ["plan_ready"]
