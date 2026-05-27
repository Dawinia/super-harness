"""Reducer tests — full-rebuild derive_state per lifecycle-event-model §3.8.

Pins the 5 invariants from §3.8.5:
1. Idempotent (same input → same output)
2. Rebuildable (state.yaml round-trip — Task 1.7 territory; here we test idempotence)
3. Prefix consistency (state at N = derive(events[:N]))
4. Tolerant of truncated last line
5. event_counts excludes unknown event types
"""
import logging
from pathlib import Path

from super_harness.core.events import Actor, Event
from super_harness.core.reducer import derive_state
from super_harness.core.ulid import new_event_id
from super_harness.core.writer import EventWriter
from tests.unit.core.test_writer import _make_event


def _seed(tmp: Path, types: list[str], change_id: str = "c1") -> Path:
    f = tmp / "events.jsonl"
    w = EventWriter(f)
    for t in types:
        w.emit(_make_event(change_id, t), skip_validation=(t in ("implementation_complete",)))
    return f


def test_reducer_idempotent(tmp_path: Path):
    f = _seed(tmp_path, ["intent_declared", "plan_ready"])
    a = derive_state(f)
    b = derive_state(f)
    assert a == b


def test_reducer_prefix_consistency(tmp_path: Path):
    f = _seed(tmp_path, ["intent_declared", "plan_ready", "plan_approved"])
    full = derive_state(f)
    assert full["c1"].current_state == "PLAN_APPROVED"
    # rewriting with only first 2 events should produce AWAITING_PLAN_REVIEW
    prefix = Path(str(f) + ".prefix")
    prefix.write_text("\n".join(f.read_text().splitlines()[:2]) + "\n")
    assert derive_state(prefix)["c1"].current_state == "AWAITING_PLAN_REVIEW"


def test_reducer_skips_unknown_event_types(tmp_path: Path):
    f = tmp_path / "events.jsonl"
    w = EventWriter(f)
    w.emit(_make_event("c1", "intent_declared"))
    # inject an unknown event by direct append (bypassing writer)
    with open(f, "a") as fp:
        fp.write('{"event_id":"ev_x","type":"banana","change_id":"c1",'
                 '"timestamp":"2026-05-27T10:00:00Z","actor":{"type":"adapter","identifier":"x"},'
                 '"framework":"plain"}\n')
    state = derive_state(f)
    assert "banana" not in state["c1"].event_counts


def test_reducer_tolerant_to_partial_last_line(tmp_path: Path):
    f = tmp_path / "events.jsonl"
    w = EventWriter(f)
    w.emit(_make_event("c1", "intent_declared"))
    with open(f, "a") as fp:
        fp.write('{"event_id":"ev_y","type":"plan_re')  # truncated line, no newline
    state = derive_state(f)
    assert state["c1"].current_state == "INTENT_DECLARED"


def test_reducer_isolates_changes_by_id(tmp_path: Path):
    """Multi-change_id interleaved replay: per-change state must not bleed across changes."""
    f = tmp_path / "events.jsonl"
    w = EventWriter(f)
    # interleave two changes
    w.emit(_make_event("c1", "intent_declared"))
    w.emit(_make_event("c2", "intent_declared"))
    w.emit(_make_event("c1", "plan_ready"))
    w.emit(_make_event("c2", "plan_ready"))
    w.emit(_make_event("c1", "plan_approved"))
    # c2 still in AWAITING_PLAN_REVIEW; c1 has advanced
    state = derive_state(f)
    assert state["c1"].current_state == "PLAN_APPROVED"
    assert state["c2"].current_state == "AWAITING_PLAN_REVIEW"
    assert state["c1"].event_counts == {"intent_declared": 1, "plan_ready": 1, "plan_approved": 1}
    assert state["c2"].event_counts == {"intent_declared": 1, "plan_ready": 1}


def test_reducer_illegal_transition_preserves_state(tmp_path: Path, caplog):
    """INVALID transition: current_state preserved, last_event_* updated, log.warning emitted."""
    f = tmp_path / "events.jsonl"
    w = EventWriter(f)
    w.emit(_make_event("c1", "intent_declared"))
    # `merged` from INTENT_DECLARED is illegal (per transition table)
    # bypass emit-time validation to put it on disk
    w.emit(_make_event("c1", "merged"), skip_validation=True)
    with caplog.at_level(logging.WARNING, logger="super_harness.reducer"):
        state = derive_state(f)
    # state preserved at INTENT_DECLARED
    assert state["c1"].current_state == "INTENT_DECLARED"
    # last_event_* updated to the rejected event (audit trail)
    assert state["c1"].last_event_type == "merged"
    # warning emitted
    assert any("illegal transition" in r.message for r in caplog.records)


def test_reducer_clock_drift_warning_fires(tmp_path: Path, caplog):
    """Clock drift > threshold emits warning but does not reorder."""
    f = tmp_path / "events.jsonl"
    # craft two events with timestamps out of order by 10 minutes
    w = EventWriter(f)
    ev1 = Event(
        event_id=new_event_id(), type="intent_declared", change_id="c1",
        timestamp="2026-05-27T10:10:00Z",
        actor=Actor(type="adapter", identifier="test"),
        framework="plain", payload={},
    )
    ev2 = Event(
        event_id=new_event_id(), type="plan_ready", change_id="c1",
        timestamp="2026-05-27T10:00:00Z",  # 10 min EARLIER than ev1 (clock drift)
        actor=Actor(type="adapter", identifier="test"),
        framework="plain", payload={},
    )
    w.emit(ev1)
    w.emit(ev2)
    with caplog.at_level(logging.WARNING, logger="super_harness.reducer"):
        state = derive_state(f)
    # append order respected — plan_ready still moves state forward
    assert state["c1"].current_state == "AWAITING_PLAN_REVIEW"
    assert any("timestamp drift" in r.message for r in caplog.records)


def _ev(change_id: str, event_type: str, framework: str) -> Event:
    """Local helper — _make_event hardcodes framework='plain'; we need to vary it."""
    return Event(
        event_id=new_event_id(), type=event_type, change_id=change_id,
        timestamp="2026-05-27T10:00:00Z",
        actor=Actor(type="adapter", identifier="test"),
        framework=framework,  # type: ignore[arg-type]
        payload={},
    )


def test_reducer_preserves_framework_across_non_declaration_events(tmp_path: Path):
    """Non-declaration events (plan_ready, intent_abandoned, etc.) must NOT clobber framework.

    Bug context: CLI `change abandon` and sensor-emitted events default framework='plain'
    because their actor has no framework context. The reducer previously assigned
    cs.framework = ev.framework unconditionally, erasing the original framework choice
    from state.yaml even though events.jsonl preserved it.
    """
    f = tmp_path / "events.jsonl"
    w = EventWriter(f)
    w.emit(_ev("c1", "intent_declared", "openspec"))
    w.emit(_ev("c1", "plan_ready", "plain"))       # sensor-emitted default
    w.emit(_ev("c1", "intent_abandoned", "plain"))  # CLI-emitted default
    state = derive_state(f)
    assert state["c1"].framework == "openspec"


def test_reducer_intent_redeclared_can_change_framework(tmp_path: Path):
    """intent_redeclared IS allowed to update framework (user explicitly switches).

    Distinguishes the fix from a blanket 'never update framework after first event' —
    redeclaration is the canonical channel for switching frameworks mid-change.
    """
    f = tmp_path / "events.jsonl"
    w = EventWriter(f)
    w.emit(_ev("c1", "intent_declared", "openspec"))
    w.emit(_ev("c1", "intent_redeclared", "spec-kit"))
    state = derive_state(f)
    assert state["c1"].framework == "spec-kit"
