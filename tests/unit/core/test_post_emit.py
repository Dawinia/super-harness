"""Tests for `post_emit_refresh` helper (Task 1.9 / B-3 fix).

Per super-harness v0.1 Phase 1 plan: every emit site (CLI commands, dispatcher
result handler, adapter observe loop) must call `refresh_state_after_emit`
synchronously after writing to events.jsonl, so state.yaml never lags the event
stream. v0.1 = full rebuild per lifecycle-event-model §3.8.2.
"""
from pathlib import Path

from super_harness.core.post_emit import refresh_state_after_emit
from super_harness.core.state_yaml import read_state_yaml
from super_harness.core.writer import EventWriter
from tests.unit.core.test_writer import _make_event


def test_refresh_writes_state_yaml(tmp_path: Path):
    harness = tmp_path / ".harness"
    harness.mkdir()
    events = harness / "events.jsonl"
    state = harness / "state.yaml"
    EventWriter(events).emit(_make_event("c1"))
    refresh_state_after_emit(tmp_path)
    assert state.exists()
    data = read_state_yaml(state)
    assert "c1" in data["changes"]
    assert data["changes"]["c1"]["current_state"] == "INTENT_DECLARED"


def test_refresh_with_no_events_file(tmp_path: Path):
    """refresh_state_after_emit must not crash if events.jsonl doesn't yet exist.

    Edge case: first emit fails before writer creates the file, but caller still
    calls refresh. Should be a no-op that produces an empty state.yaml.
    """
    harness = tmp_path / ".harness"
    harness.mkdir()
    refresh_state_after_emit(tmp_path)
    state = harness / "state.yaml"
    assert state.exists()
    data = read_state_yaml(state)
    assert data["changes"] == {}
    assert data["last_reduced_event_id"] == ""


def test_refresh_serializes_multiple_calls(tmp_path: Path):
    """Multiple consecutive refresh calls must produce a stable result.

    No real concurrency test here (that needs subprocess + fcntl); this just
    verifies the lock acquire/release cycle works repeatedly in one process.
    """
    harness = tmp_path / ".harness"
    harness.mkdir()
    w = EventWriter(harness / "events.jsonl")
    for t in ("intent_declared", "plan_ready", "plan_approved"):
        w.emit(_make_event("c1", t))
    refresh_state_after_emit(tmp_path)
    refresh_state_after_emit(tmp_path)  # second call same result
    refresh_state_after_emit(tmp_path)  # third too
    data = read_state_yaml(harness / "state.yaml")
    assert data["changes"]["c1"]["current_state"] == "PLAN_APPROVED"
