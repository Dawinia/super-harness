"""state.yaml serialization round-trip + atomic write tests.

Per plan Task 1.7 (v0.1 Phase 1). The 3rd test cements lifecycle §3.8.5
invariant 2 (Rebuildable: `derive_state(events) ↔ ChangeState.from_yaml`).
"""
from pathlib import Path

from super_harness.core.reducer import derive_state
from super_harness.core.state import ChangeState
from super_harness.core.state_yaml import read_state_yaml, write_state_yaml
from super_harness.core.writer import EventWriter
from tests.unit.core.test_writer import _make_event


def test_write_then_read_round_trip(tmp_path: Path) -> None:
    cs = ChangeState(
        change_id="c1",
        current_state="PLAN_APPROVED",
        framework="openspec",
        last_event_id="ev_x",
        last_event_type="plan_approved",
        last_event_at="2026-05-27T10:00:00Z",
        event_counts={"intent_declared": 1, "plan_ready": 1, "plan_approved": 1},
        description="add foo",
        tier="Normal",
        scope={"files": ["src/foo.ts"]},
        affected_anchors=["capability-foo"],
    )
    f = tmp_path / "state.yaml"
    write_state_yaml(f, {"c1": cs}, last_reduced_event_id="ev_x")
    out = read_state_yaml(f)
    assert "c1" in out["changes"]
    assert out["changes"]["c1"]["current_state"] == "PLAN_APPROVED"


def test_write_is_atomic(tmp_path: Path) -> None:
    f = tmp_path / "state.yaml"
    write_state_yaml(f, {}, last_reduced_event_id="ev_x")
    # no .tmp file left behind
    assert not (tmp_path / "state.yaml.tmp").exists()
    assert f.exists()


def test_reducer_round_trip_via_state_yaml(tmp_path: Path) -> None:
    """Invariant 2 (§3.8.5): derive_state(events) round-trips losslessly through state.yaml."""
    events_file = tmp_path / "events.jsonl"
    w = EventWriter(events_file)
    for t in ["intent_declared", "plan_ready", "plan_approved", "implementation_started"]:
        w.emit(_make_event("c1", t))

    derived = derive_state(events_file)
    state_file = tmp_path / "state.yaml"
    write_state_yaml(state_file, derived, last_reduced_event_id=derived["c1"].last_event_id)
    reloaded = read_state_yaml(state_file)

    assert reloaded["changes"]["c1"]["current_state"] == "IMPLEMENTATION_IN_PROGRESS"
    assert reloaded["changes"]["c1"]["event_counts"] == {
        "intent_declared": 1,
        "plan_ready": 1,
        "plan_approved": 1,
        "implementation_started": 1,
    }
    assert reloaded["changes"]["c1"]["framework"] == "plain"
    assert reloaded["last_reduced_event_id"] == derived["c1"].last_event_id
