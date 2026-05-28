"""Tests for SensorDispatcher (sensor-gate-architecture §3.3, §3.6 #1).

Covers happy path, parallel execution, batch wall-clock timeout with
`sensor_timeout_exceeded` auto-emit, crash with `sensor_crashed` auto-emit,
trigger matching (events + activities), extension event stamping (actor),
EmitPreconditionError tolerance, empty sensor list, and state.yaml refresh
wiring (B-3).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import ClassVar

import pytest

from super_harness.core.events import Actor, Event
from super_harness.core.writer import EventWriter
from super_harness.sensors import (
    Activity,
    ActivityType,
    Determinism,
    Sensor,
    SensorResult,
    WorkspaceContext,
)
from super_harness.sensors.dispatcher import SensorDispatcher

# --- helpers ---------------------------------------------------------------


def _read_events(events_file: Path) -> list[str]:
    if not events_file.exists():
        return []
    return [line for line in events_file.read_text().splitlines() if line.strip()]


def _mk_event(etype: str = "plan_ready", change_id: str = "c1") -> Event:
    return Event(
        event_id="ev_a",
        type=etype,
        change_id=change_id,
        timestamp="2026-05-27T10:00:00Z",
        actor=Actor(type="adapter", identifier="t"),
        framework="plain",
    )


def _setup_harness(tmp_path: Path) -> tuple[EventWriter, WorkspaceContext, Path]:
    """Create `.harness/` so refresh_state_after_emit can write state.yaml."""
    harness = tmp_path / ".harness"
    harness.mkdir()
    events_file = harness / "events.jsonl"
    writer = EventWriter(events_file)
    ctx = WorkspaceContext(workspace_root=tmp_path)
    return writer, ctx, events_file


# --- sensor fixtures -------------------------------------------------------


class _Slow(Sensor):
    name: ClassVar[str] = "slow"
    version: ClassVar[str] = "0.1.0"
    triggers_on_events: ClassVar[tuple[str, ...]] = ("plan_ready",)
    determinism: ClassVar[Determinism] = "computational"

    def check(self, trigger, ctx):  # type: ignore[no-untyped-def]
        time.sleep(0.05)
        return SensorResult(status="pass", summary="slow ok")


class _Boom(Sensor):
    name: ClassVar[str] = "boom"
    version: ClassVar[str] = "0.1.0"
    triggers_on_events: ClassVar[tuple[str, ...]] = ("plan_ready",)
    determinism: ClassVar[Determinism] = "computational"

    def check(self, trigger, ctx):  # type: ignore[no-untyped-def]
        raise RuntimeError("kaboom")


class _Sleeper(Sensor):
    """Slow enough to trigger a sub-second timeout test."""

    name: ClassVar[str] = "sleeper"
    version: ClassVar[str] = "0.1.0"
    triggers_on_events: ClassVar[tuple[str, ...]] = ("plan_ready",)
    determinism: ClassVar[Determinism] = "computational"

    def check(self, trigger, ctx):  # type: ignore[no-untyped-def]
        time.sleep(0.5)
        return SensorResult(status="pass", summary="never")


class _Emits(Sensor):
    """Emits a single extension event on every check."""

    name: ClassVar[str] = "emits"
    version: ClassVar[str] = "0.2.0"
    triggers_on_events: ClassVar[tuple[str, ...]] = ("plan_ready",)
    determinism: ClassVar[Determinism] = "computational"

    def check(self, trigger, ctx):  # type: ignore[no-untyped-def]
        # Emit an event with the same change_id (which has already reached
        # AWAITING_PLAN_REVIEW after plan_ready) — plan_approved is the
        # legal next event for that state.
        ev = Event(
            event_id="",  # dispatcher stamps a fresh ULID
            type="plan_approved",
            change_id=trigger.change_id,
            timestamp="",  # dispatcher stamps `now()`
            actor=Actor(type="adapter", identifier="placeholder"),
            framework="plain",
        )
        return SensorResult(status="pass", summary="ok", emit_events=[ev])


class _NonMatcher(Sensor):
    name: ClassVar[str] = "nope"
    version: ClassVar[str] = "0.1.0"
    triggers_on_events: ClassVar[tuple[str, ...]] = ("foo",)
    determinism: ClassVar[Determinism] = "computational"

    def __init__(self) -> None:
        super().__init__()
        self.called = 0

    def check(self, trigger, ctx):  # type: ignore[no-untyped-def]
        self.called += 1
        return SensorResult(status="pass", summary="ran")


class _ActivityEcho(Sensor):
    name: ClassVar[str] = "act-echo"
    version: ClassVar[str] = "0.1.0"
    triggers_on_events: ClassVar[tuple[str, ...]] = ()
    triggers_on_activities: ClassVar[tuple[ActivityType, ...]] = ("commit",)
    determinism: ClassVar[Determinism] = "computational"

    def __init__(self) -> None:
        super().__init__()
        self.called = 0

    def check(self, trigger, ctx):  # type: ignore[no-untyped-def]
        self.called += 1
        return SensorResult(status="pass", summary="seen")


class _BadEmit(Sensor):
    """Emits an event that violates emit-time preconditions."""

    name: ClassVar[str] = "bad-emit"
    version: ClassVar[str] = "0.1.0"
    triggers_on_events: ClassVar[tuple[str, ...]] = ("plan_ready",)
    determinism: ClassVar[Determinism] = "computational"

    def check(self, trigger, ctx):  # type: ignore[no-untyped-def]
        # `merged` is illegal from AWAITING_PLAN_REVIEW (only legal from
        # READY_TO_MERGE per transitions.py).
        ev = Event(
            event_id="",
            type="merged",
            change_id=trigger.change_id,
            timestamp="",
            actor=Actor(type="adapter", identifier="x"),
            framework="plain",
        )
        return SensorResult(status="pass", summary="bad", emit_events=[ev])


# Records each sensor's check() start time so the parallel test can assert
# concurrent start (overhead-independent) rather than an absolute wall-clock
# bound (fragile on slow CI — dispatcher fixed overhead can swamp the sleep).
_PARALLEL_STARTS: list[float] = []


class _ParallelTwoA(Sensor):
    name: ClassVar[str] = "par-a"
    version: ClassVar[str] = "0.1.0"
    triggers_on_events: ClassVar[tuple[str, ...]] = ("plan_ready",)
    determinism: ClassVar[Determinism] = "computational"

    def check(self, trigger, ctx):  # type: ignore[no-untyped-def]
        _PARALLEL_STARTS.append(time.monotonic())
        time.sleep(0.1)
        return SensorResult(status="pass", summary="a")


class _ParallelTwoB(Sensor):
    name: ClassVar[str] = "par-b"
    version: ClassVar[str] = "0.1.0"
    triggers_on_events: ClassVar[tuple[str, ...]] = ("plan_ready",)
    determinism: ClassVar[Determinism] = "computational"

    def check(self, trigger, ctx):  # type: ignore[no-untyped-def]
        _PARALLEL_STARTS.append(time.monotonic())
        time.sleep(0.1)
        return SensorResult(status="pass", summary="b")


# --- tests -----------------------------------------------------------------


def test_dispatcher_runs_matching(tmp_path: Path) -> None:
    writer = EventWriter(tmp_path / "events.jsonl")
    d = SensorDispatcher(
        [_Slow()],
        writer=writer,
        context=WorkspaceContext(workspace_root=tmp_path),
    )
    d.on_event_emit(_mk_event())
    # _Slow returns pass with no emit_events → events.jsonl stays empty.
    assert _read_events(tmp_path / "events.jsonl") == []


def test_dispatcher_emits_sensor_crashed(tmp_path: Path) -> None:
    writer = EventWriter(tmp_path / "events.jsonl")
    d = SensorDispatcher(
        [_Boom()],
        writer=writer,
        context=WorkspaceContext(workspace_root=tmp_path),
    )
    d.on_event_emit(_mk_event())
    lines = _read_events(tmp_path / "events.jsonl")
    assert any('"type":"sensor_crashed"' in line for line in lines)
    # actor.type=sensor + identifier carries name@version
    assert any('"identifier":"boom@0.1.0"' in line for line in lines)


def test_dispatcher_emits_sensor_timeout_exceeded(tmp_path: Path) -> None:
    writer = EventWriter(tmp_path / "events.jsonl")
    d = SensorDispatcher(
        [_Sleeper()],
        writer=writer,
        context=WorkspaceContext(workspace_root=tmp_path),
        timeout_s=0.1,
    )
    d.on_event_emit(_mk_event())
    lines = _read_events(tmp_path / "events.jsonl")
    assert any('"type":"sensor_timeout_exceeded"' in line for line in lines)
    assert any('"identifier":"sleeper@0.1.0"' in line for line in lines)


def test_dispatcher_runs_sensors_in_parallel(tmp_path: Path) -> None:
    """Two sleep(0.1) sensors start concurrently (parallel), not 0.1s apart (serial).

    Asserts the gap between the two sensors' check() start times — a concurrency
    signal that is independent of total wall-clock and the dispatcher's fixed
    overhead. An absolute elapsed-time bound is fragile on slow CI runners, where
    dispatcher overhead can swamp the 0.1s parallel-vs-serial difference (a 0.1s
    sleep parallel run was observed at ~0.24s on a loaded macOS CI box).
    """
    _PARALLEL_STARTS.clear()
    writer = EventWriter(tmp_path / "events.jsonl")
    d = SensorDispatcher(
        [_ParallelTwoA(), _ParallelTwoB()],
        writer=writer,
        context=WorkspaceContext(workspace_root=tmp_path),
    )
    d.on_event_emit(_mk_event())
    assert len(_PARALLEL_STARTS) == 2, f"expected 2 sensor starts, got {_PARALLEL_STARTS}"
    start_gap = abs(_PARALLEL_STARTS[1] - _PARALLEL_STARTS[0])
    # Parallel: both threads pick up their task near-simultaneously (gap ~ms).
    # Serial: the 2nd sensor would start only after the 1st's 0.1s sleep (gap ≥0.1s).
    # 0.05s cleanly separates the two regardless of CI slowness / dispatcher overhead.
    assert start_gap < 0.05, (
        f"sensors started {start_gap:.3f}s apart; expected near-concurrent start "
        f"(parallel). A ≥0.1s gap means serial execution."
    )


def test_dispatcher_emits_extension_events_from_sensor_result(tmp_path: Path) -> None:
    writer, ctx, events_file = _setup_harness(tmp_path)
    # Seed: intent_declared + plan_ready so plan_approved is legal next.
    EventWriter(events_file).emit(
        Event(
            event_id="ev_seed1",
            type="intent_declared",
            change_id="c1",
            timestamp="2026-05-27T10:00:00Z",
            actor=Actor(type="human", identifier="me"),
            framework="plain",
        )
    )
    EventWriter(events_file).emit(
        Event(
            event_id="ev_seed2",
            type="plan_ready",
            change_id="c1",
            timestamp="2026-05-27T10:01:00Z",
            actor=Actor(type="human", identifier="me"),
            framework="plain",
        )
    )

    d = SensorDispatcher([_Emits()], writer=writer, context=ctx)
    d.on_event_emit(_mk_event(etype="plan_ready", change_id="c1"))

    lines = _read_events(events_file)
    assert any('"type":"plan_approved"' in line for line in lines)
    # actor stamped to sensor@version
    assert any('"identifier":"emits@0.2.0"' in line for line in lines)
    # event_id was empty in SensorResult; dispatcher must have stamped a fresh one
    assert any('"event_id":"ev_' in line for line in lines if "plan_approved" in line)


def test_dispatcher_skips_non_matching_sensors(tmp_path: Path) -> None:
    writer = EventWriter(tmp_path / "events.jsonl")
    sensor = _NonMatcher()
    d = SensorDispatcher(
        [sensor],
        writer=writer,
        context=WorkspaceContext(workspace_root=tmp_path),
    )
    d.on_event_emit(_mk_event(etype="plan_ready"))  # sensor triggers on "foo"
    assert sensor.called == 0
    assert _read_events(tmp_path / "events.jsonl") == []


def test_dispatcher_handles_activity_trigger(tmp_path: Path) -> None:
    writer = EventWriter(tmp_path / "events.jsonl")
    sensor = _ActivityEcho()
    d = SensorDispatcher(
        [sensor],
        writer=writer,
        context=WorkspaceContext(workspace_root=tmp_path),
    )
    d.on_activity(Activity(type="commit", change_id="c1"))
    assert sensor.called == 1


def test_dispatcher_handles_emit_precondition_error_gracefully(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    writer, ctx, events_file = _setup_harness(tmp_path)
    # Seed intent_declared + plan_ready so AWAITING_PLAN_REVIEW is current;
    # _BadEmit will then try to emit merged → illegal.
    EventWriter(events_file).emit(
        Event(
            event_id="ev_seed1",
            type="intent_declared",
            change_id="c1",
            timestamp="2026-05-27T10:00:00Z",
            actor=Actor(type="human", identifier="me"),
            framework="plain",
        )
    )
    EventWriter(events_file).emit(
        Event(
            event_id="ev_seed2",
            type="plan_ready",
            change_id="c1",
            timestamp="2026-05-27T10:01:00Z",
            actor=Actor(type="human", identifier="me"),
            framework="plain",
        )
    )

    d = SensorDispatcher([_BadEmit()], writer=writer, context=ctx)
    import logging as _logging

    with caplog.at_level(_logging.WARNING):
        # Must not raise.
        d.on_event_emit(_mk_event(etype="plan_ready", change_id="c1"))

    lines = _read_events(events_file)
    # The rejected event must NOT appear in events.jsonl.
    assert not any('"type":"merged"' in line for line in lines)
    # A WARNING about the rejected emit should have been logged.
    assert any(
        rec.levelno == _logging.WARNING and "merged" in rec.message
        for rec in caplog.records
    )


def test_dispatcher_with_empty_sensors_list(tmp_path: Path) -> None:
    writer = EventWriter(tmp_path / "events.jsonl")
    d = SensorDispatcher(
        [],
        writer=writer,
        context=WorkspaceContext(workspace_root=tmp_path),
    )
    # No-op for both event and activity triggers.
    d.on_event_emit(_mk_event())
    d.on_activity(Activity(type="commit", change_id="c1"))
    assert _read_events(tmp_path / "events.jsonl") == []


def test_dispatcher_refreshes_state_after_emit(tmp_path: Path) -> None:
    """A successful sensor emit must trigger state.yaml refresh (B-3 wiring)."""
    writer, ctx, events_file = _setup_harness(tmp_path)
    EventWriter(events_file).emit(
        Event(
            event_id="ev_seed1",
            type="intent_declared",
            change_id="c1",
            timestamp="2026-05-27T10:00:00Z",
            actor=Actor(type="human", identifier="me"),
            framework="plain",
        )
    )
    EventWriter(events_file).emit(
        Event(
            event_id="ev_seed2",
            type="plan_ready",
            change_id="c1",
            timestamp="2026-05-27T10:01:00Z",
            actor=Actor(type="human", identifier="me"),
            framework="plain",
        )
    )

    d = SensorDispatcher([_Emits()], writer=writer, context=ctx)
    d.on_event_emit(_mk_event(etype="plan_ready", change_id="c1"))

    state_file = tmp_path / ".harness" / "state.yaml"
    assert state_file.exists(), "refresh_state_after_emit was not called"
    # After plan_approved, state must be PLAN_APPROVED.
    from super_harness.core.state_yaml import read_state_yaml

    data = read_state_yaml(state_file)
    assert data["changes"]["c1"]["current_state"] == "PLAN_APPROVED"
