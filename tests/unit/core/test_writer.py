import json
import os
import subprocess
import sys
import textwrap
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytest

from super_harness.core.events import Actor, Event
from super_harness.core.ulid import new_event_id
from super_harness.core.writer import EventWriter


def _make_event(change_id: str, event_type: str = "intent_declared") -> Event:
    return Event(
        event_id=new_event_id(),
        type=event_type,
        change_id=change_id,
        timestamp="2026-05-27T10:00:00Z",
        actor=Actor(type="adapter", identifier="test"),
        framework="plain",
        payload={"description": "x"},
    )


def test_writer_creates_parent_dir(tmp_path: Path):
    events_file = tmp_path / ".harness" / "events.jsonl"
    assert not events_file.parent.exists()
    EventWriter(events_file)  # constructor should mkdir parents
    assert events_file.parent.exists()


def test_writer_appends_one_event(tmp_path: Path):
    events_file = tmp_path / "events.jsonl"
    w = EventWriter(events_file)
    w.emit(_make_event("c1"))
    lines = events_file.read_text().splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["change_id"] == "c1"
    assert parsed["type"] == "intent_declared"


def test_writer_appends_multiple_events(tmp_path: Path):
    events_file = tmp_path / "events.jsonl"
    w = EventWriter(events_file)
    for i in range(5):
        w.emit(_make_event(f"c{i}"))
    lines = events_file.read_text().splitlines()
    assert len(lines) == 5
    ids = [json.loads(line)["change_id"] for line in lines]
    assert ids == ["c0", "c1", "c2", "c3", "c4"]  # append order preserved


def test_writer_threaded_concurrent_append_no_loss(tmp_path: Path):
    """Multi-thread same-process: 100 threads x 1 event each, 0 loss + 0 torn lines."""
    events_file = tmp_path / "events.jsonl"
    w = EventWriter(events_file)
    n = 100

    def emit_one(i: int) -> None:
        w.emit(_make_event(f"c{i}"))

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(as_completed([pool.submit(emit_one, i) for i in range(n)]))

    lines = events_file.read_text().splitlines()
    assert len(lines) == n
    parsed = [json.loads(line) for line in lines]  # all parse as valid JSON
    assert len({p["change_id"] for p in parsed}) == n  # no dupes


def test_writer_multi_process_append_no_loss(tmp_path: Path):
    """Multi-PROCESS concurrent append: 4 subprocesses x 25 events.

    Round 2 I-7 fix: same-process ThreadPoolExecutor is GIL-serialized for the
    actual write() syscall, so it doesn't truly exercise spec §3.9 #1
    (multi-agent concurrent append). This spawns real subprocesses.
    """
    events_file = tmp_path / "events.jsonl"
    worker_script = tmp_path / "worker.py"
    worker_script.write_text(textwrap.dedent(f"""
        import sys
        from super_harness.core.events import Actor, Event
        from super_harness.core.ulid import new_event_id
        from super_harness.core.writer import EventWriter

        wid = sys.argv[1]
        w = EventWriter(r"{events_file}")
        for i in range(25):
            w.emit(Event(
                event_id=new_event_id(),
                type="intent_declared",
                change_id=f"w{{wid}}-{{i}}",
                timestamp="2026-05-27T10:00:00Z",
                actor=Actor(type="adapter", identifier=f"worker{{wid}}"),
                framework="plain",
                payload={{"description": "x"}},
            ))
    """))

    procs = [
        subprocess.Popen(
            [sys.executable, str(worker_script), str(i)],
            env={**os.environ},
            stderr=subprocess.PIPE,
        )
        for i in range(4)
    ]
    for p in procs:
        rc = p.wait(timeout=30)
        if rc != 0:
            err = p.stderr.read().decode() if p.stderr else ""
            raise AssertionError(f"worker exited with {rc}: {err}")

    lines = events_file.read_text().splitlines()
    assert len(lines) == 100, f"expected 100 lines, got {len(lines)}"
    # Each line must parse cleanly (no torn writes mid-line)
    parsed = [json.loads(line) for line in lines]
    # All 100 change_ids unique (no event loss, no duplicates)
    assert len({p["change_id"] for p in parsed}) == 100
    # All 4 workers represented
    workers = {p["actor"]["identifier"] for p in parsed}
    assert workers == {"worker0", "worker1", "worker2", "worker3"}


def test_writer_skip_validation_kwarg_exists(tmp_path: Path):
    """Task 1.5 will add emit-time validation; for now, skip_validation kwarg
    is a no-op pass-through (defaults False). This test pins the API shape so
    Task 1.5 can wire validation without breaking existing callers."""
    events_file = tmp_path / "events.jsonl"
    w = EventWriter(events_file)
    w.emit(_make_event("c1"), skip_validation=True)  # must not raise
    assert events_file.exists()


def test_writer_skip_validation_bypasses_illegal(tmp_path: Path):
    """skip_validation=True must bypass emit-time validation even for illegal events.

    Pins the polarity of the `if not skip_validation` guard so a future
    refactor that flips it would fail loudly. (Without this test, the kwarg
    could be silently inverted and the existing happy-path test would still
    pass.)
    """
    events_file = tmp_path / "events.jsonl"
    w = EventWriter(events_file)
    # plan_ready as first event is illegal (must follow intent_declared);
    # default emit would raise EmitPreconditionError. skip_validation=True bypasses.
    w.emit(_make_event("c1", "plan_ready"), skip_validation=True)
    assert events_file.read_text().count("\n") == 1


# F3 (review 2026-07-02): the writer is the single choke point to disk and does
# NOT round-trip parse_event_line, so it needs its own type-only timestamp
# guard — shape, not a transition precondition, hence independent of
# skip_validation.

def test_writer_rejects_non_string_timestamp(tmp_path: Path):
    import dataclasses
    from datetime import datetime, timezone

    from super_harness.core.writer import EmitPreconditionError

    events_file = tmp_path / "events.jsonl"
    w = EventWriter(events_file)
    for bad_ts in (123, datetime(2026, 5, 27, tzinfo=timezone.utc)):
        ev = dataclasses.replace(_make_event("c1"), timestamp=bad_ts)  # type: ignore[arg-type]
        with pytest.raises(EmitPreconditionError, match="timestamp"):
            w.emit(ev, skip_validation=True)
    assert not events_file.exists() or events_file.read_text() == ""


def test_writer_accepts_empty_string_timestamp(tmp_path: Path):
    # type-only guard: "" is a str (the dispatcher stamps blanks before emit)
    import dataclasses

    events_file = tmp_path / "events.jsonl"
    w = EventWriter(events_file)
    w.emit(dataclasses.replace(_make_event("c1"), timestamp=""), skip_validation=True)
    assert events_file.read_text().count("\n") == 1
