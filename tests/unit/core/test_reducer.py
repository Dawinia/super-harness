"""Reducer tests — full-rebuild derive_state per lifecycle-event-model §3.8.

Pins the 5 invariants from §3.8.5:
1. Idempotent (same input → same output)
2. Rebuildable (state.yaml round-trip — Task 1.7 territory; here we test idempotence)
3. Prefix consistency (state at N = derive(events[:N]))
4. Tolerant of truncated last line
5. event_counts excludes unknown event types
"""
from pathlib import Path

from super_harness.core.reducer import derive_state
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
